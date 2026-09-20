"""Deterministic predictive-analysis helpers for the hosted agent (forecast, time-to-limit, risk ranking, OEE outlook).

These are TREND and OUTLOOK tools, not failure predictors. There is no failure history and no trained model behind
them, so nothing here ever produces a probability of failure or a remaining useful life. What they do produce is
arithmetic on observed data with its uncertainty stated: a line fitted to recent history with a prediction
interval, the time until that line would cross an alarm limit, a triage ordering of the fleet, and what a
persistent quality gap costs in units. As with snapshot.py, the model only narrates; the numbers, the "is there a
trend" verdict and the reliability flags are computed here so they can be unit-tested (tests/test_analysis.py).

No agent-framework or Azure imports.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import median
from typing import Any

import snapshot as snap

SIGNALS = snap.SIGNALS
UNITS = snap.UNITS

FIT_WINDOW_MIN = 180          # history used for a fit
MIN_POINTS = 30               # fewer 1-minute points than this: no fit
TREND_T = 5.0                 # |t| of the slope must reach this; deliberately strict (real data is autocorrelated)
MAX_HORIZON_H = 72            # a crossing further out than this is extrapolation, not a forecast
Z95 = 1.96

# Illustrative alarm limits. Vibration: ISO 10816-1 class I (small machines) guideline zone boundaries in mm/s RMS:
# A/B 0.71, B/C 1.8, C/D 4.5. The machine class of each asset is NOT in the data, so these are labelled as
# a generic guideline; the caller can always pass an explicit limit. No generic limit exists for temperature or
# pressure, so none is invented: the tool asks for one.
DEFAULT_LIMITS: dict[str, dict[str, Any]] = {
    "vibration_mms": {"alert": 1.8, "danger": 4.5,
                      "source": "ISO 10816-1 class I guideline zone boundaries (illustrative; machine class not in the data)"},
}

# Triage thresholds (fleet-relative quality; see rank_fleet)
FPY_HIGH_GAP_PTS = 15.0
FPY_MEDIUM_GAP_PTS = 5.0
CYCLE_HIGH_RATIO = 1.25
CYCLE_MEDIUM_RATIO = 1.10
IDEAL_CYCLE_S = 4.0           # the Jumpstart's own ideal cycle time in its Performance() function

TIER_ORDER = {"HIGH": 0, "MEDIUM": 1, "UNKNOWN": 2, "LOW": 3}


# --------------------------------------------------------------------------- KQL builders

def series_kql(asset_id: str, signal: str, window_min: int = FIT_WINDOW_MIN) -> str:
    """1-minute values of one signal for one asset, ending at that asset's newest reading."""
    a = snap.validate_asset_id(asset_id)
    if signal not in SIGNALS:
        raise ValueError(f"signal must be one of {list(SIGNALS)}")
    w = int(window_min)
    return (
        f"let a = '{a}';\n"
        f"let tmax = toscalar(pdm_telemetry() | where asset_id == a | summarize max(ts));\n"
        f"pdm_telemetry() | where asset_id == a and ts > tmax - {w}m and isnotnull({signal})\n"
        f"| project ts, value = {signal} | order by ts asc"
    )


def fleet_series_kql(window_min: int = 120) -> str:
    w = int(window_min)
    return (
        "let tmax = toscalar(pdm_telemetry() | summarize max(ts));\n"
        f"pdm_telemetry() | where ts > tmax - {w}m\n"
        f"| project asset_id, ts, {', '.join(SIGNALS)} | order by asset_id asc, ts asc"
    )


def fleet_stats_kql() -> str:
    """Recent (30 min) and baseline statistics per asset in one query; same windows as snapshot.py."""
    aggs = ", ".join(f"a_{i} = avg({s}), sd_{i} = stdev({s})" for i, s in enumerate(SIGNALS))
    return (
        "let tmax = toscalar(pdm_telemetry() | summarize max(ts));\n"
        f"pdm_telemetry()\n"
        f"| extend phase = case(ts > tmax - {snap.RECENT_WINDOW}, 'recent', "
        f"ts between (tmax - {snap.BASELINE_LOOKBACK} .. tmax - {snap.BASELINE_EXCLUDE}), 'base', 'skip')\n"
        f"| where phase != 'skip'\n"
        f"| summarize n = count(), newest = max(ts), {aggs} by asset_id, phase"
    )


def quality_kql(window_h: int = 3) -> str:
    """Production quality per machine, ending at the newest production record (not the wall clock)."""
    return (
        "let tq = toscalar(production_quality | summarize max(timestamp));\n"
        f"production_quality | where timestamp > tq - {int(window_h)}h\n"
        "| summarize items = count(), fpy = 100.0 * avg(todouble(first_pass_yield)), cycle_s = avg(todouble(cycle_time_seconds)),\n"
        "            oldest = min(timestamp), newest = max(timestamp) by machine_id"
    )


# --------------------------------------------------------------------------- statistics

def fit_line(xs: list[float], ys: list[float]) -> dict[str, float]:
    """Ordinary least squares y = intercept + slope * x. x in minutes."""
    n = len(xs)
    if n < 3 or n != len(ys):
        raise ValueError("need at least 3 paired points")
    xm, ym = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("x values are all equal")
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = ym - slope * xm
    ss_tot = sum((y - ym) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    resid_sd = math.sqrt(ss_res / (n - 2)) if n > 2 else 0.0
    se_slope = resid_sd / math.sqrt(sxx)
    t = slope / se_slope if se_slope > 0 else (math.inf if slope else 0.0)
    return {"n": n, "slope": slope, "intercept": intercept, "x_mean": xm, "sxx": sxx, "y_mean": ym,
            "r2": (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0, "resid_sd": resid_sd, "se_slope": se_slope, "t": t}


def predict(fit: dict[str, float], x: float) -> tuple[float, float]:
    """Fitted value at x and the half-width of an approximate 95 % prediction interval."""
    yhat = fit["intercept"] + fit["slope"] * x
    half = Z95 * fit["resid_sd"] * math.sqrt(1 + 1 / fit["n"] + (x - fit["x_mean"]) ** 2 / fit["sxx"])
    return yhat, half


def has_trend(fit: dict[str, float]) -> bool:
    return abs(fit["t"]) >= TREND_T


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00") if value else None


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _prepare(rows: list[dict[str, Any]], now: datetime | None) -> tuple[list[float], list[float], datetime, int]:
    """Rows of {ts, value} -> x (minutes relative to the newest reading, <= 0), y, newest, data age in minutes."""
    now = now or datetime.now(timezone.utc)
    pts = sorted(((_to_dt(r["ts"]), float(r["value"])) for r in rows if r.get("value") is not None), key=lambda p: p[0])
    newest = pts[-1][0]
    xs = [(t - newest).total_seconds() / 60.0 for t, _ in pts]
    return xs, [v for _, v in pts], newest, max(0, int((now - newest).total_seconds() // 60))


# --------------------------------------------------------------------------- 1. forecast and time-to-limit

def forecast_signal(asset_id: str, signal: str, rows: list[dict[str, Any]], horizon_min: int = 60,
                    now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"asset_id": asset_id, "signal": signal, "unit": UNITS.get(signal), "horizon_min": horizon_min,
                           "as_of_utc": _iso(now)}
    if len(rows) < MIN_POINTS:
        out.update(status="INSUFFICIENT_HISTORY", points=len(rows), min_points=MIN_POINTS,
                   summary=f"Only {len(rows)} one-minute points; need at least {MIN_POINTS} for a fit. No forecast.")
        return out
    xs, ys, newest, age = _prepare(rows, now)
    fit = fit_line(xs, ys)
    history_min = xs[-1] - xs[0]
    trend = has_trend(fit)
    slope_h = fit["slope"] * 60
    ci_h = Z95 * fit["se_slope"] * 60
    if trend:
        expected, half = predict(fit, horizon_min)
        basis = "fitted line"
    else:
        expected, half = fit["y_mean"], Z95 * fit["resid_sd"] * math.sqrt(1 + 1 / fit["n"])
        basis = "flat level (no significant trend)"
    notes: list[str] = []
    reliability = "OK"
    if horizon_min > history_min / 2:
        reliability = "LOW"
        notes.append(f"Horizon {horizon_min} min is more than half of the {history_min:.0f} min of history used; treat as extrapolation.")
    if age > snap.STALE_AFTER_MINUTES:
        reliability = "LOW"
        notes.append(f"Data is stale: newest reading is {age} minutes old; the horizon is counted from that reading.")
    out.update(
        status="TREND" if trend else "NO_SIGNIFICANT_TREND",
        newest_reading_utc=_iso(newest), data_age_minutes=age, data_stale=age > snap.STALE_AFTER_MINUTES,
        history_minutes=round(history_min), points=fit["n"],
        slope_per_hour=round(slope_h, 4), slope_ci95_per_hour=[round(slope_h - ci_h, 4), round(slope_h + ci_h, 4)],
        t_stat=round(fit["t"], 1), r_squared=round(fit["r2"], 4), residual_sd=round(fit["resid_sd"], 4),
        current_level=round(predict(fit, 0)[0] if trend else fit["y_mean"], 4),
        forecast={"minutes_after_newest_reading": horizon_min, "basis": basis, "expected": round(expected, 4),
                  "interval95_low": round(expected - half, 4), "interval95_high": round(expected + half, 4)},
        reliability=reliability, notes=notes,
    )
    out["summary"] = (
        f"{signal}: {'trend %+.3f %s/h' % (slope_h, UNITS[signal]) if trend else 'no significant trend (slope %+.3f %s/h, |t|=%.1f < %.0f)' % (slope_h, UNITS[signal], abs(fit['t']), TREND_T)}; "
        f"expected {expected:.3f} {UNITS[signal]} in {horizon_min} min (95% interval {expected - half:.3f} to {expected + half:.3f})."
        + (" Reliability LOW." if reliability == "LOW" else ""))
    return out


def time_to_limit(asset_id: str, signal: str, rows: list[dict[str, Any]], limit: float | None = None,
                  direction: str = "above", now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if direction not in ("above", "below"):
        raise ValueError("direction must be 'above' or 'below'")
    out: dict[str, Any] = {"asset_id": asset_id, "signal": signal, "unit": UNITS.get(signal), "direction": direction,
                           "as_of_utc": _iso(now)}
    limit_source = "supplied by the caller"
    if limit is None:
        cfg = DEFAULT_LIMITS.get(signal)
        if not cfg:
            out.update(status="NO_LIMIT_CONFIGURED", summary=f"No alarm limit is known for {signal}; ask the user for one (no generic limit exists).")
            return out
        limit, limit_source = cfg["alert"], cfg["source"]
        direction = "above"
        out["direction"] = direction
    out.update(limit=limit, limit_source=limit_source)
    if len(rows) < MIN_POINTS:
        out.update(status="INSUFFICIENT_HISTORY", points=len(rows), summary=f"Only {len(rows)} points; need at least {MIN_POINTS}. No estimate.")
        return out
    xs, ys, newest, age = _prepare(rows, now)
    fit = fit_line(xs, ys)
    trend = has_trend(fit)
    level = predict(fit, 0)[0] if trend else fit["y_mean"]
    sd = fit["resid_sd"]
    out.update(newest_reading_utc=_iso(newest), data_age_minutes=age, data_stale=age > snap.STALE_AFTER_MINUTES,
               current_level=round(level, 4), residual_sd=round(sd, 4), margin_to_limit=round(limit - level if direction == "above" else level - limit, 4),
               margin_in_sd=round((limit - level if direction == "above" else level - limit) / sd, 1) if sd else None,
               slope_per_hour=round(fit["slope"] * 60, 4), t_stat=round(fit["t"], 1), history_minutes=round(xs[-1] - xs[0]))
    beyond = level >= limit if direction == "above" else level <= limit
    if beyond:
        out.update(status="ALREADY_BEYOND", summary=f"Current level {level:.3f} {UNITS[signal]} is already {direction} the limit {limit}.")
        return out
    toward = fit["slope"] > 0 if direction == "above" else fit["slope"] < 0
    if not trend or not toward:
        out.update(status="NO_CROSSING_EXPECTED",
                   summary=(f"No significant trend toward the limit ({'moving away' if trend and not toward else 'flat'}); level {level:.3f} {UNITS[signal]} "
                            f"sits {abs(out['margin_in_sd']) if out['margin_in_sd'] is not None else 'n/a'} noise-sd from {limit}. "
                            "A sudden step change cannot be foreseen from this data."))
        return out
    slope = fit["slope"]                       # per minute
    hours = (limit - level) / slope / 60 if direction == "above" else (level - limit) / -slope / 60
    lo_s, hi_s = fit["slope"] - Z95 * fit["se_slope"], fit["slope"] + Z95 * fit["se_slope"]
    bounds = []
    for s in (lo_s, hi_s):
        if (s > 0) == (direction == "above") and s != 0:
            bounds.append((limit - level) / s / 60 if direction == "above" else (level - limit) / -s / 60)
    earliest = min(bounds) if bounds else None
    latest = max(bounds) if len(bounds) == 2 else None
    out.update(hours_to_limit=round(hours, 1), hours_to_limit_earliest=round(earliest, 1) if earliest is not None else None,
               hours_to_limit_latest=round(latest, 1) if latest is not None else None)
    if hours > MAX_HORIZON_H:
        out.update(status="BEYOND_HORIZON", summary=f"The fitted trend would reach {limit} in about {hours:.0f} h, beyond the {MAX_HORIZON_H} h horizon this can support. Not a forecast.")
    else:
        out.update(status="CROSSING_ESTIMATED",
                   summary=f"At the fitted trend ({slope * 60:+.3f} {UNITS[signal]}/h) the limit {limit} is reached in about {hours:.1f} h (earliest {earliest:.1f} h)."
                           if earliest is not None else
                           f"At the fitted trend the limit {limit} is reached in about {hours:.1f} h; the slope interval includes zero on one side, so the latest time is unbounded.")
    if age > snap.STALE_AFTER_MINUTES:
        out["summary"] += f" DATA IS STALE ({age} min old): time counts from the newest reading."
    return out


# --------------------------------------------------------------------------- 2. OEE / quality outlook

def oee_outlook(asset_id: str, quality_rows: dict[str, dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """What a persistent quality / cycle-time gap costs, from production_quality only. quality_rows: machine_id(str) -> row."""
    now = now or datetime.now(timezone.utc)
    row = quality_rows.get(str(asset_id))
    out: dict[str, Any] = {"asset_id": asset_id, "as_of_utc": _iso(now)}
    if not row or not row.get("items"):
        out.update(status="NO_PRODUCTION_DATA", summary="No production_quality records for this machine.")
        return out
    span_h = max((_to_dt(row["newest"]) - _to_dt(row["oldest"])).total_seconds() / 3600.0, 1e-6)
    fleet = [r for r in quality_rows.values() if r.get("items")]
    fleet_fpy, fleet_cycle = median(float(r["fpy"]) for r in fleet), median(float(r["cycle_s"]) for r in fleet)
    items_h = row["items"] / span_h
    fpy, cycle = float(row["fpy"]), float(row["cycle_s"])
    gap_pts = fleet_fpy - fpy
    lost_good_day = max(0.0, gap_pts / 100.0 * items_h * 24)
    cap_now, cap_typ = 3600.0 / cycle, 3600.0 / fleet_cycle
    age = max(0, int((now - _to_dt(row["newest"])).total_seconds() // 60))
    out.update(
        status="OK", window_hours=round(span_h, 2), items=int(row["items"]), items_per_hour=round(items_h, 1),
        newest_production_utc=_iso(_to_dt(row["newest"])), data_age_minutes=age, data_stale=age > snap.STALE_AFTER_MINUTES,
        first_pass_yield_pct=round(fpy, 1), fleet_median_fpy_pct=round(fleet_fpy, 1), fpy_gap_pts=round(gap_pts, 1),
        cycle_time_s=round(cycle, 2), fleet_median_cycle_s=round(fleet_cycle, 2), ideal_cycle_s=IDEAL_CYCLE_S,
        performance_pct=round(min(100.0, 100.0 * IDEAL_CYCLE_S / cycle), 1),
        capacity_gap_items_per_hour=round(max(0.0, cap_typ - cap_now), 1),
        lost_good_units_per_day_if_persists=round(lost_good_day),
        assumptions=["Same throughput and mix continue.", "Compared with the median of the machines in the same window, not a target.",
                     "No cost data exists; units only.", "A persistent offset is not a trend: this says what the gap costs, not that it will worsen."],
    )
    # Cross-check two fields that must agree: items/hour recorded vs what the cycle time allows (one item per cycle).
    implied_items_h = 3600.0 / cycle
    warnings: list[str] = []
    if items_h > 1.5 * implied_items_h:
        warnings.append(f"Data inconsistency: {items_h:.0f} items/h were recorded but a {cycle:.2f} s cycle allows only about "
                        f"{implied_items_h:.0f}/h. The unit figures below rest on the recorded counts and are illustrative, not reliable.")
    out["implied_items_per_hour_from_cycle"] = round(implied_items_h)
    out["warnings"] = warnings
    out["summary"] = (f"FPY {fpy:.1f}% vs fleet median {fleet_fpy:.1f}% ({gap_pts:+.1f} pts); cycle {cycle:.2f}s vs {fleet_cycle:.2f}s. "
                      f"If it persists at {items_h:.0f} items/h: about {lost_good_day:,.0f} good units lost per 24 h against the fleet median."
                      + ("".join(" WARNING: " + w for w in warnings)))
    if age > snap.STALE_AFTER_MINUTES:
        out["summary"] += f" PRODUCTION DATA IS STALE ({age} min old)."
    return out


def build_fleet_inputs(stats_rows: list[dict[str, Any]], series_rows: list[dict[str, Any]],
                       now: datetime | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, float]]]]:
    """Rows from fleet_stats_kql() and fleet_series_kql() -> (per-asset snapshots, per-asset per-signal line fits)."""
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for r in stats_rows:
        by.setdefault(str(r["asset_id"]), {})[r["phase"]] = r
    snapshots: dict[str, dict[str, Any]] = {}
    for aid, phases in by.items():
        rc, bs = phases.get("recent"), phases.get("base")
        recent = {"n": rc["n"], "newest": rc["newest"], **{s: rc[f"a_{i}"] for i, s in enumerate(SIGNALS)}} if rc else None
        base = ({"n": bs["n"], **{f"avg_{s}": bs[f"a_{i}"] for i, s in enumerate(SIGNALS)},
                 **{f"sd_{s}": bs[f"sd_{i}"] for i, s in enumerate(SIGNALS)}}) if bs else None
        snapshots[aid] = snap.build_snapshot(aid, recent, base, now=now)
    per_asset: dict[str, list[dict[str, Any]]] = {}
    for r in series_rows:
        per_asset.setdefault(str(r["asset_id"]), []).append(r)
    trends: dict[str, dict[str, dict[str, float]]] = {}
    for aid, rows in per_asset.items():
        rows.sort(key=lambda r: _to_dt(r["ts"]))
        t0 = _to_dt(rows[-1]["ts"])
        xs = [(_to_dt(r["ts"]) - t0).total_seconds() / 60.0 for r in rows]
        trends[aid] = {}
        for s in SIGNALS:
            pairs = [(x, float(r[s])) for x, r in zip(xs, rows) if r.get(s) is not None]
            if len(pairs) >= MIN_POINTS:
                trends[aid][s] = fit_line([p[0] for p in pairs], [p[1] for p in pairs])
    return snapshots, trends


# --------------------------------------------------------------------------- 3. fleet triage ranking

def rank_fleet(snapshots: dict[str, dict[str, Any]], trends: dict[str, dict[str, dict[str, float]]],
               quality_rows: dict[str, dict[str, Any]], names: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Order machines by how much attention they need. This is a triage ordering of OBSERVED deviations,
    not a probability of failure."""
    fleet = [r for r in quality_rows.values() if r.get("items")]
    fleet_fpy = median(float(r["fpy"]) for r in fleet) if fleet else None
    fleet_cycle = median(float(r["cycle_s"]) for r in fleet) if fleet else None
    ranked = []
    for aid, s in snapshots.items():
        reasons, high, medium = [], False, False
        magnitude = 0.0
        for sig in s.get("abnormal_signals", []):
            sv = s["signals"][sig]
            high = True
            magnitude = max(magnitude, abs(sv.get("z_score") or 0))
            reasons.append(f"{sig} {sv['delta_pct']:+.1f}% vs own baseline (z={sv['z_score']}) - ABNORMAL")
        q = quality_rows.get(str(aid))
        if q and q.get("items") and fleet_fpy is not None:
            gap = fleet_fpy - float(q["fpy"])
            ratio = float(q["cycle_s"]) / fleet_cycle if fleet_cycle else 1.0
            if gap >= FPY_HIGH_GAP_PTS:
                high = True; magnitude = max(magnitude, gap); reasons.append(f"first-pass yield {float(q['fpy']):.1f}% is {gap:.1f} pts below the fleet median {fleet_fpy:.1f}%")
            elif gap >= FPY_MEDIUM_GAP_PTS:
                medium = True; reasons.append(f"first-pass yield {gap:.1f} pts below the fleet median")
            if ratio >= CYCLE_HIGH_RATIO:
                high = True; reasons.append(f"cycle time {float(q['cycle_s']):.2f}s is {100 * (ratio - 1):.0f}% slower than the fleet median {fleet_cycle:.2f}s")
            elif ratio >= CYCLE_MEDIUM_RATIO:
                medium = True; reasons.append(f"cycle time {100 * (ratio - 1):.0f}% slower than the fleet median")
        for sig, fit in (trends.get(aid) or {}).items():
            if has_trend(fit):
                medium = True
                reasons.append(f"{sig} trending {fit['slope'] * 60:+.3f} {UNITS[sig]}/h over the last {fit['n']} min (|t|={abs(fit['t']):.1f})")
        no_baseline = s.get("signals") and all(v["status"] in ("INSUFFICIENT_BASELINE", "NO_DATA") for v in s["signals"].values())
        if not s.get("signals") or no_baseline:
            tier = "UNKNOWN"; reasons.append("no telemetry or no usable baseline: cannot judge")
        else:
            tier = "HIGH" if high else "MEDIUM" if medium else "LOW"
            if s.get("data_stale"):
                reasons.append(f"data is stale ({s.get('data_age_minutes')} min old)")
        info = names.get(str(aid), {})
        ranked.append({"asset_id": aid, "asset_name": info.get("asset_name"), "plant_name": info.get("plant_name"), "tier": tier,
                       "reasons": reasons or ["all signals normal vs own baseline; no quality gap; no significant trend"],
                       "_mag": magnitude})
    ranked.sort(key=lambda r: (TIER_ORDER[r["tier"]], -r["_mag"], r["asset_id"]))
    for r in ranked:
        r.pop("_mag")
    return {"ranking": ranked,
            "counts": {t: sum(1 for r in ranked if r["tier"] == t) for t in TIER_ORDER},
            "note": "Triage ordering from observed deviations (own-baseline sensor shifts, fleet-relative quality, significant trends). "
                    "It is NOT a probability of failure: there is no failure history and no trained model."}
