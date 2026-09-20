"""Offline tests for the predictive-analysis helpers (hosted_agent/analysis.py).

The important properties: pure noise must NOT be reported as a trend (the live Jumpstart data is exactly that), a real
ramp must be, limit crossings must be timed correctly, and nothing may ever read as a probability of failure.
"""
from __future__ import annotations

import importlib.util
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HA = Path(__file__).resolve().parents[1] / "src" / "pdmops" / "foundry" / "hosted_agent"
sys.path.insert(0, str(HA))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HA / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


snap = _load("snapshot")
an = _load("analysis")

NOW = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)


def series(values, end_minutes_ago=2):
    """Rows of {ts, value} one minute apart, the last one `end_minutes_ago` before NOW."""
    n = len(values)
    return [{"ts": NOW - timedelta(minutes=end_minutes_ago + (n - 1 - i)), "value": v} for i, v in enumerate(values)]


def noise(n=180, mean=1.1, sd=0.09, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, sd) for _ in range(n)]


def ramp(n=180, start=1.0, per_hour=0.4, sd=0.05, seed=2):
    rng = random.Random(seed)
    return [start + per_hour * i / 60 + rng.gauss(0, sd) for i in range(n)]


# ---- fit / forecast ----------------------------------------------------------

def test_fit_line_recovers_a_known_slope():
    xs = [float(i) for i in range(60)]
    fit = an.fit_line(xs, [2.0 + 0.5 * x for x in xs])
    assert fit["slope"] == pytest.approx(0.5) and fit["r2"] == pytest.approx(1.0)


def test_pure_noise_is_not_a_trend_across_many_seeds():
    false_trends = sum(an.has_trend(an.fit_line([float(i) for i in range(180)], noise(seed=s))) for s in range(200))
    assert false_trends <= 2, f"{false_trends}/200 noise series reported as a trend"


def test_noise_forecast_is_flat_with_an_interval():
    f = an.forecast_signal("103", "vibration_mms", series(noise()), horizon_min=60, now=NOW)
    assert f["status"] == "NO_SIGNIFICANT_TREND"
    assert f["forecast"]["basis"].startswith("flat")
    assert abs(f["forecast"]["expected"] - 1.1) < 0.05
    assert f["forecast"]["interval95_low"] < f["forecast"]["expected"] < f["forecast"]["interval95_high"]


def test_ramp_is_detected_and_forecast_follows_it():
    f = an.forecast_signal("103", "vibration_mms", series(ramp()), horizon_min=60, now=NOW)
    assert f["status"] == "TREND" and f["slope_per_hour"] == pytest.approx(0.4, abs=0.05)
    last = 1.0 + 0.4 * 179 / 60                       # value at the newest reading
    assert f["forecast"]["expected"] == pytest.approx(last + 0.4 * 60 / 60, abs=0.15)   # horizon is counted from it
    lo, hi = f["slope_ci95_per_hour"]                 # a 95 % interval misses the true slope 1 time in 20, so assert shape, not coverage
    assert lo < f["slope_per_hour"] < hi and (hi - lo) < 0.05


def test_long_horizon_and_stale_data_lower_the_reliability():
    assert an.forecast_signal("1", "temp_c", series(noise()), horizon_min=150, now=NOW)["reliability"] == "LOW"
    stale = an.forecast_signal("1", "temp_c", series(noise(), end_minutes_ago=50), horizon_min=30, now=NOW)
    assert stale["reliability"] == "LOW" and stale["data_stale"] is True and any("stale" in n for n in stale["notes"])


def test_too_little_history_gives_no_forecast():
    f = an.forecast_signal("1", "temp_c", series(noise(n=10)), now=NOW)
    assert f["status"] == "INSUFFICIENT_HISTORY" and "forecast" not in f


# ---- time to limit -----------------------------------------------------------

def test_flat_signal_never_crosses_and_says_a_step_cannot_be_foreseen():
    r = an.time_to_limit("103", "vibration_mms", series(noise()), now=NOW)          # default ISO alert limit 1.8
    assert r["status"] == "NO_CROSSING_EXPECTED" and "step change" in r["summary"]
    assert r["limit"] == 1.8 and "ISO 10816" in r["limit_source"] and r["margin_in_sd"] > 5


def test_ramp_crossing_is_timed_with_an_interval():
    rows = series(ramp(start=1.0, per_hour=0.4))                                      # ends near 2.2 - already past 1.8
    assert an.time_to_limit("1", "vibration_mms", rows, limit=1.8, now=NOW)["status"] == "ALREADY_BEYOND"
    r = an.time_to_limit("1", "vibration_mms", series(ramp(start=0.6, per_hour=0.4)), limit=1.8, now=NOW)   # ends ~1.8 - 0.4 h ...
    assert r["status"] in ("CROSSING_ESTIMATED", "ALREADY_BEYOND")
    r = an.time_to_limit("1", "vibration_mms", series(ramp(start=0.5, per_hour=0.4)), limit=2.5, now=NOW)
    assert r["status"] == "CROSSING_ESTIMATED"
    assert r["hours_to_limit_earliest"] <= r["hours_to_limit"] <= (r["hours_to_limit_latest"] or 1e9)


def test_moving_away_from_the_limit_is_not_a_crossing():
    r = an.time_to_limit("1", "vibration_mms", series(ramp(start=2.0, per_hour=-0.4)), limit=3.0, now=NOW)
    assert r["status"] == "NO_CROSSING_EXPECTED"


def test_a_crossing_beyond_the_horizon_is_refused():
    r = an.time_to_limit("1", "vibration_mms", series(ramp(start=1.0, per_hour=0.05, sd=0.005)), limit=50.0, now=NOW)
    assert r["status"] == "BEYOND_HORIZON" and "Not a forecast" in r["summary"]


def test_no_limit_is_invented_for_temperature_or_pressure():
    for sig in ("temp_c", "pressure_bar"):
        r = an.time_to_limit("1", sig, series(noise()), now=NOW)
        assert r["status"] == "NO_LIMIT_CONFIGURED"
    assert an.time_to_limit("1", "temp_c", series(noise(mean=40, sd=1)), limit=80, now=NOW)["status"] == "NO_CROSSING_EXPECTED"


def test_kql_builders_validate_inputs():
    with pytest.raises(ValueError):
        an.series_kql("x'; .drop table t //", "temp_c")
    with pytest.raises(ValueError):
        an.series_kql("103", "current_a")                                            # not a Jumpstart signal
    k = an.series_kql("103", "temp_c", 120)
    assert "pdm_telemetry()" in k and "ts > tmax - 120m" in k and "telemetry_enriched" not in k
    assert "ts > tmax" in an.fleet_series_kql() and "phase" in an.fleet_stats_kql() and "production_quality" in an.quality_kql()


# ---- OEE outlook -------------------------------------------------------------

def q(fpy, cycle, items=6850, hours=3.0):
    return {"items": items, "fpy": fpy, "cycle_s": cycle, "oldest": NOW - timedelta(hours=hours, minutes=1), "newest": NOW - timedelta(minutes=1)}


QUALITY = {str(i): q(88.0 + (i % 3) * 0.3, 4.25) for i in (101, 102, 104, 105, 106, 107, 108)} | {"103": q(63.5, 6.48)}


def test_oee_outlook_prices_the_persistent_gap_in_units():
    o = an.oee_outlook("103", QUALITY, now=NOW)
    assert o["status"] == "OK" and o["fpy_gap_pts"] > 20
    assert o["items_per_hour"] == pytest.approx(6850 / 3.0, rel=0.02)
    assert o["lost_good_units_per_day_if_persists"] == pytest.approx(o["fpy_gap_pts"] / 100 * o["items_per_hour"] * 24, rel=0.01)
    assert o["performance_pct"] < 65 and o["capacity_gap_items_per_hour"] > 0
    assert any("No cost data" in a for a in o["assumptions"]) and any("not a trend" in a for a in o["assumptions"])


def test_oee_outlook_flags_recorded_throughput_that_the_cycle_time_cannot_explain():
    # Found live: 2283 items/h recorded while a 6.48 s cycle allows ~555/h - the simulated fields contradict each other.
    o = an.oee_outlook("103", QUALITY, now=NOW)
    assert o["implied_items_per_hour_from_cycle"] == 556 and o["warnings"] and "inconsistency" in o["warnings"][0]
    assert "WARNING" in o["summary"]
    consistent = dict(QUALITY, **{"103": q(63.5, 6.48, items=int(3600 / 6.48 * 3))})
    assert an.oee_outlook("103", consistent, now=NOW)["warnings"] == []


def test_oee_outlook_for_a_typical_machine_is_about_zero_and_unknown_machine_is_reported():
    assert an.oee_outlook("101", QUALITY, now=NOW)["lost_good_units_per_day_if_persists"] < 300
    assert an.oee_outlook("999", QUALITY, now=NOW)["status"] == "NO_PRODUCTION_DATA"


# ---- fleet ranking -----------------------------------------------------------

def _snap(aid, vib=1.1, abnormal=False):
    recent = {"n": 30, "newest": NOW - timedelta(minutes=2), "vibration_mms": vib, "temp_c": 40.0, "pressure_bar": 3.0}
    base = {"n": 52, "avg_vibration_mms": 1.1, "sd_vibration_mms": 0.1, "avg_temp_c": 40.0, "sd_temp_c": 1.5,
            "avg_pressure_bar": 3.0, "sd_pressure_bar": 0.35}
    return snap.build_snapshot(aid, recent, base, now=NOW)


def test_ranking_puts_the_quality_outlier_first_and_never_claims_a_probability():
    snaps = {aid: _snap(aid) for aid in QUALITY}
    names = {aid: {"asset_name": f"m{aid}", "plant_name": "P"} for aid in QUALITY}
    r = an.rank_fleet(snaps, {}, QUALITY, names)
    assert r["ranking"][0]["asset_id"] == "103" and r["ranking"][0]["tier"] == "HIGH"
    assert r["counts"]["HIGH"] == 1 and r["counts"]["LOW"] == 7
    assert any("first-pass yield" in x for x in r["ranking"][0]["reasons"]) and any("cycle time" in x for x in r["ranking"][0]["reasons"])
    assert "NOT a probability of failure" in r["note"]


def test_ranking_uses_abnormal_sensors_trends_and_unknowns():
    snaps = {"a": _snap("a", vib=1.1 + 0.9), "b": _snap("b"), "c": snap.build_snapshot("c", {"n": 0}, {"n": 0}, now=NOW)}
    trend_fit = an.fit_line([float(i) for i in range(180)], ramp())
    r = an.rank_fleet(snaps, {"b": {"vibration_mms": trend_fit}}, {}, {})
    tiers = {x["asset_id"]: x["tier"] for x in r["ranking"]}
    assert tiers == {"a": "HIGH", "b": "MEDIUM", "c": "UNKNOWN"}
    assert [x["asset_id"] for x in r["ranking"]] == ["a", "b", "c"]


# ---- fleet assembly and agent wiring ------------------------------------------

def test_build_fleet_inputs_turns_query_rows_into_snapshots_and_fits():
    stats = []
    for aid in ("101", "102"):
        stats.append({"asset_id": aid, "phase": "recent", "n": 30, "newest": NOW - timedelta(minutes=1),
                      "a_0": 1.1, "sd_0": 0.1, "a_1": 40.0, "sd_1": 1.5, "a_2": 3.0, "sd_2": 0.3})
        stats.append({"asset_id": aid, "phase": "base", "n": 60, "newest": NOW - timedelta(minutes=40),
                      "a_0": 1.1, "sd_0": 0.1, "a_1": 40.0, "sd_1": 1.5, "a_2": 3.0, "sd_2": 0.3})
    series = [{"asset_id": "101", "ts": NOW - timedelta(minutes=120 - i), "vibration_mms": 1.0 + 0.01 * i, "temp_c": 40.0, "pressure_bar": 3.0}
              for i in range(120)]
    snaps, trends = an.build_fleet_inputs(stats, series, now=NOW)
    assert set(snaps) == {"101", "102"} and snaps["101"]["summary"].startswith("All signals NORMAL")
    assert an.has_trend(trends["101"]["vibration_mms"]) and "102" not in trends


def test_agent_registers_the_analysis_tools_and_forbids_failure_probabilities():
    main = (HA / "main.py").read_text(encoding="utf-8")
    for tool in ("forecast_signal", "time_to_limit", "risk_ranking", "oee_outlook"):
        assert f"def {tool}(" in main
    tools_line = next(l for l in main.splitlines() if l.strip().startswith("tools: list"))
    assert all(t in tools_line for t in ("forecast_signal", "time_to_limit", "risk_ranking", "oee_outlook", "asset_snapshot"))
    assert "NEVER state a probability of failure" in main and "remaining useful life" in main
    assert "import analysis as an" in main
