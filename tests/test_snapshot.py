"""Offline tests for the deterministic asset snapshot (hosted_agent/snapshot.py).

They encode failures seen while testing the live agent: a +100% vibration rise called normal,
an unchanged signal grouped with changed ones, and stale data called fresh. Signals are those
of the Real-Time Manufacturing Jumpstart: vibration (mm/s), temperature (degC), pressure (bar).
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src" / "pdmops" / "foundry" / "hosted_agent" / "snapshot.py"
spec = importlib.util.spec_from_file_location("snapshot", MODULE)
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)  # type: ignore[union-attr]

NOW = datetime(2026, 9, 20, 11, 30, tzinfo=timezone.utc)


def rows(vib_recent=2.9, temp_recent=40.2, press_recent=3.02, newest_minutes_ago=37, base_n=52):
    recent = {"n": 30, "newest": NOW - timedelta(minutes=newest_minutes_ago),
              "vibration_mms": vib_recent, "temp_c": temp_recent, "pressure_bar": press_recent}
    base = {"n": base_n,
            "avg_vibration_mms": 1.15, "sd_vibration_mms": 0.12,
            "avg_temp_c": 40.0, "sd_temp_c": 1.6,
            "avg_pressure_bar": 3.0, "sd_pressure_bar": 0.35}
    return recent, base


def test_signals_are_the_jumpstart_ones_and_have_units():
    assert snap.SIGNALS == ("vibration_mms", "temp_c", "pressure_bar")   # no current sensor in the Jumpstart
    s = snap.build_snapshot("103", *rows(), now=NOW)
    assert {k: v["unit"] for k, v in s["signals"].items()} == {"vibration_mms": "mm/s", "temp_c": "degC", "pressure_bar": "bar"}


def test_large_vibration_rise_is_abnormal_and_unchanged_signals_are_not_grouped_with_it():
    s = snap.build_snapshot("103", *rows(), now=NOW)
    assert s["signals"]["vibration_mms"]["status"] == "ABNORMAL"
    assert s["signals"]["vibration_mms"]["delta_pct"] > 100
    assert s["signals"]["temp_c"]["status"] == "NORMAL"          # +0.5 %, well inside its own variation
    assert s["signals"]["pressure_bar"]["status"] == "NORMAL"
    assert s["abnormal_signals"] == ["vibration_mms"]


def test_the_shipped_fault_scenario_is_flagged():
    # The Jumpstart's own SENSOR_BIAS scenario for machine 103: +21 degC and +3.5 mm/s.
    s = snap.build_snapshot("103", *rows(vib_recent=1.15 + 3.5, temp_recent=40.0 + 21), now=NOW)
    assert set(s["abnormal_signals"]) == {"vibration_mms", "temp_c"}


def test_pure_noise_is_not_flagged():
    # A 30-minute mean of uniform noise stays close to the baseline mean: nothing should read ABNORMAL.
    s = snap.build_snapshot("101", *rows(vib_recent=1.19, temp_recent=40.6, press_recent=2.93), now=NOW)
    assert s["abnormal_signals"] == []
    assert "All signals NORMAL" in s["summary"]


def test_stale_data_is_flagged_with_its_age():
    s = snap.build_snapshot("103", *rows(newest_minutes_ago=37), now=NOW)
    assert s["data_age_minutes"] == 37 and s["data_stale"] is True and "STALE" in s["summary"]
    assert snap.build_snapshot("103", *rows(newest_minutes_ago=3), now=NOW)["data_stale"] is False


def test_insufficient_baseline_never_reads_as_normal():
    s = snap.build_snapshot("108", *rows(base_n=5), now=NOW)
    assert all(v["status"] == "INSUFFICIENT_BASELINE" for v in s["signals"].values())
    assert "Do not call the asset normal" in s["summary"]


def test_small_change_is_normal_even_if_baseline_is_very_tight():
    recent, base = rows(vib_recent=1.15 * 1.03)
    base["sd_vibration_mms"] = 0.001                 # >3 sigma but only +3 %: needs BOTH conditions
    assert snap.build_snapshot("A", recent, base, now=NOW)["signals"]["vibration_mms"]["status"] == "NORMAL"


def test_large_percent_change_inside_noise_is_normal():
    recent, base = rows(press_recent=3.0 * 1.10)
    base["sd_pressure_bar"] = 2.0                    # very noisy: +10 % is < 1 sigma
    assert snap.build_snapshot("A", recent, base, now=NOW)["signals"]["pressure_bar"]["status"] == "NORMAL"


def test_unknown_asset_reports_no_telemetry():
    s = snap.build_snapshot("999", {"n": 0}, {"n": 0}, now=NOW)
    assert s["data_stale"] is True and "NO TELEMETRY" in s["summary"]


def test_kql_reads_the_pdm_layer_and_the_asset_id_is_validated():
    for bad in ("x'; .drop table machineraw //", "a b", "", "x" * 65, "a|b"):
        with pytest.raises(ValueError):
            snap.recent_kql(bad)
        with pytest.raises(ValueError):
            snap.baseline_kql(bad)
    recent, baseline = snap.recent_kql("103"), snap.baseline_kql("103")
    assert "pdm_telemetry()" in recent and "pdm_telemetry()" in baseline
    assert "telemetry_enriched" not in recent + baseline        # the deleted hand-built schema
    assert "vibration_mms" in recent and "current_a" not in recent + baseline
    assert "ts between" in baseline
