"""Deterministic asset health snapshot used by the hosted agent's `asset_snapshot` tool.

Why this exists: the model was asked to compare recent readings with a baseline itself and
got it wrong in ways that matter for a maintenance tool - it called a +100% vibration rise
"within typical ranges", grouped an unchanged signal with changed ones, and guessed how old
the data was. Arithmetic, thresholds and staleness are computed HERE, in code, and the model
only narrates the result.

No agent-framework or Azure imports, so it is unit-testable offline (tests/test_snapshot.py).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# Signals of the Real-Time Manufacturing Jumpstart (see docs/jumpstart-mapping.md). There is no current sensor.
SIGNALS = ("vibration_mms", "temp_c", "pressure_bar")
UNITS = {"vibration_mms": "mm/s", "temp_c": "degC", "pressure_bar": "bar"}

RECENT_WINDOW = "30m"        # "now" = the newest reading for the asset, not the wall clock
BASELINE_LOOKBACK = "14d"
BASELINE_EXCLUDE = "30m"     # baseline stops this long before the newest reading, so a developing fault
                             # cannot contaminate its own baseline. The Jumpstart holds only a few hours of history;
                             # raise this (2h-6h) once weeks of data exist so slow drifts are caught too.
MIN_BASELINE_POINTS = 30
MATERIAL_Z = 3.0             # |recent - baseline| must exceed 3 baseline standard deviations ...
MATERIAL_PCT = 5.0           # ... AND be at least 5 % of the baseline mean
STALE_AFTER_MINUTES = 15

_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def validate_asset_id(asset_id: str) -> str:
    if not isinstance(asset_id, str) or not _ASSET_ID.match(asset_id):
        raise ValueError("asset_id must be 1-64 characters of letters, digits, '_', '-' or '.'")
    return asset_id


def recent_kql(asset_id: str) -> str:
    """One row: newest reading time plus the average of each signal over the recent window."""
    a = validate_asset_id(asset_id)
    aggs = ", ".join(f"{s} = avg({s})" for s in SIGNALS)
    return (
        f"let a = '{a}';\n"
        f"let tmax = toscalar(pdm_telemetry() | where asset_id == a | summarize max(ts));\n"
        f"pdm_telemetry() | where asset_id == a and ts > tmax - {RECENT_WINDOW}\n"
        f"| summarize n = count(), newest = max(ts), {aggs}"
    )


def baseline_kql(asset_id: str) -> str:
    """One row: point count, mean and standard deviation of each signal over the baseline window."""
    a = validate_asset_id(asset_id)
    aggs = ", ".join(f"avg_{s} = avg({s}), sd_{s} = stdev({s})" for s in SIGNALS)
    return (
        f"let a = '{a}';\n"
        f"let tmax = toscalar(pdm_telemetry() | where asset_id == a | summarize max(ts));\n"
        f"pdm_telemetry() | where asset_id == a and ts between (tmax - {BASELINE_LOOKBACK} .. tmax - {BASELINE_EXCLUDE})\n"
        f"| summarize n = count(), {aggs}"
    )


def assess_signal(recent: float | None, base_avg: float | None, base_sd: float | None, base_n: int) -> dict[str, Any]:
    """Compare one signal with its own baseline. status is one of:
    NO_DATA | INSUFFICIENT_BASELINE | ABNORMAL | NORMAL."""
    if recent is None:
        return {"status": "NO_DATA"}
    out: dict[str, Any] = {"recent_avg": round(recent, 3)}
    if base_n < MIN_BASELINE_POINTS or base_avg is None:
        out.update(status="INSUFFICIENT_BASELINE", baseline_points=base_n)
        return out
    delta = recent - base_avg
    delta_pct = (delta / base_avg * 100.0) if base_avg else None
    z = (delta / base_sd) if base_sd else None
    material = (z is not None and abs(z) >= MATERIAL_Z) and (delta_pct is not None and abs(delta_pct) >= MATERIAL_PCT)
    out.update(
        baseline_avg=round(base_avg, 3),
        baseline_sd=round(base_sd, 4) if base_sd is not None else None,
        delta_pct=round(delta_pct, 1) if delta_pct is not None else None,
        z_score=round(z, 1) if z is not None else None,
        direction="up" if delta > 0 else "down" if delta < 0 else "flat",
        status="ABNORMAL" if material else "NORMAL",
    )
    return out


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def build_snapshot(asset_id: str, recent_row: dict[str, Any] | None, base_row: dict[str, Any] | None,
                   now: datetime | None = None) -> dict[str, Any]:
    """Combine the two aggregate rows into the snapshot the model narrates."""
    now = now or datetime.now(timezone.utc)
    recent_row = recent_row or {}
    base_row = base_row or {}
    newest = _parse_ts(recent_row.get("newest"))
    snapshot: dict[str, Any] = {
        "asset_id": asset_id,
        "as_of_utc": now.strftime("%Y-%m-%d %H:%M:%S+00:00"),
        "newest_reading_utc": newest.strftime("%Y-%m-%d %H:%M:%S+00:00") if newest else None,
    }
    if newest is None or not recent_row.get("n"):
        snapshot.update(data_age_minutes=None, data_stale=True, signals={},
                        summary="NO TELEMETRY found for this asset_id (check the id).")
        return snapshot

    age = max(0, int((now - newest).total_seconds() // 60))
    base_n = int(base_row.get("n") or 0)
    signals = {
        s: assess_signal(recent_row.get(s), base_row.get(f"avg_{s}"), base_row.get(f"sd_{s}"), base_n)
        for s in SIGNALS
    }
    for s, v in signals.items():
        v["unit"] = UNITS[s]
    abnormal = [s for s, v in signals.items() if v["status"] == "ABNORMAL"]
    unknown = [s for s, v in signals.items() if v["status"] in ("INSUFFICIENT_BASELINE", "NO_DATA")]
    snapshot.update(
        data_age_minutes=age,
        data_stale=age > STALE_AFTER_MINUTES,
        recent_window=f"last {RECENT_WINDOW} up to the newest reading",
        baseline_window=f"{BASELINE_LOOKBACK} back to {BASELINE_EXCLUDE} before the newest reading ({base_n} points)",
        signals=signals,
        abnormal_signals=abnormal,
        signals_without_baseline=unknown,
    )
    if abnormal:
        snapshot["summary"] = f"ABNORMAL vs own baseline: {', '.join(abnormal)}."
    elif unknown:
        snapshot["summary"] = "Cannot judge: insufficient baseline for " + ", ".join(unknown) + ". Do not call the asset normal."
    else:
        snapshot["summary"] = "All signals NORMAL vs own baseline."
    if snapshot["data_stale"]:
        snapshot["summary"] += f" DATA IS STALE: newest reading is {age} minutes old - say so first."
    return snapshot
