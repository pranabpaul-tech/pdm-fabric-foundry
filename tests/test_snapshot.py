"""Offline tests for the deterministic asset snapshot (hosted_agent/snapshot.py).

These encode the exact failures seen in the live agent test: a +100% vibration rise called
normal, an unchanged current grouped with changed signals, and stale data called fresh.
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

NOW = datetime(2026, 9, 19, 22, 12, tzinfo=timezone.utc)


def rows(vib_recent=4.02, temp_recent=66.1, cur_recent=10.8, newest_minutes_ago=37, base_n=180):
    recent = {"n": 30, "newest": NOW - timedelta(minutes=newest_minutes_ago), "vibration_rms": vib_recent,
              "temp_c": temp_recent, "current_a": cur_recent, "pressure_bar": 6.03}
    base = {"n": base_n,
            "avg_vibration_rms": 1.86, "sd_vibration_rms": 0.07,
            "avg_temp_c": 60.2, "sd_temp_c": 1.0,
            "avg_current_a": 10.61, "sd_current_a": 0.30,
            "avg_pressure_bar": 6.0, "sd_pressure_bar": 0.05}
    return recent, base


def test_doubled_vibration_is_abnormal_and_unchanged_current_is_not_grouped_with_it():
    s = snap.build_snapshot("PUMP-03", *rows(), now=NOW)
    assert s["signals"]["vibration_rms"]["status"] == "ABNORMAL"
    assert s["signals"]["vibration_rms"]["delta_pct"] > 100
    assert s["signals"]["current_a"]["status"] == "NORMAL"      # +1.8 %, well inside its own variation
    assert s["signals"]["pressure_bar"]["status"] == "NORMAL"
    assert "vibration_rms" in s["abnormal_signals"] and "current_a" not in s["abnormal_signals"]


def test_stale_data_is_flagged_with_its_age():
    s = snap.build_snapshot("PUMP-03", *rows(newest_minutes_ago=37), now=NOW)
    assert s["data_age_minutes"] == 37 and s["data_stale"] is True
    assert "STALE" in s["summary"]
    fresh = snap.build_snapshot("PUMP-03", *rows(newest_minutes_ago=3), now=NOW)
    assert fresh["data_stale"] is False


def test_insufficient_baseline_never_reads_as_normal():
    s = snap.build_snapshot("NEW-1", *rows(base_n=5), now=NOW)
    assert all(v["status"] == "INSUFFICIENT_BASELINE" for v in s["signals"].values())
    assert "Do not call the asset normal" in s["summary"]


def test_small_change_is_normal_even_if_baseline_is_very_tight():
    # +3 % but many sigma: material needs BOTH >= 3 sigma and >= 5 %
    recent, base = rows(vib_recent=1.86 * 1.03)
    base["sd_vibration_rms"] = 0.001
    s = snap.build_snapshot("A", recent, base, now=NOW)
    assert s["signals"]["vibration_rms"]["status"] == "NORMAL"


def test_large_percent_change_inside_noise_is_normal():
    recent, base = rows(cur_recent=10.61 * 1.10)
    base["sd_current_a"] = 2.0      # very noisy signal: +10 % is < 1 sigma
    s = snap.build_snapshot("A", recent, base, now=NOW)
    assert s["signals"]["current_a"]["status"] == "NORMAL"


def test_unknown_asset_reports_no_telemetry():
    s = snap.build_snapshot("NOPE", {"n": 0}, {"n": 0}, now=NOW)
    assert s["data_stale"] is True and "NO TELEMETRY" in s["summary"]


def test_asset_id_is_validated_before_being_put_in_kql():
    for bad in ("x'; .drop table telemetry_raw //", "a b", "", "x" * 65, "a|b"):
        with pytest.raises(ValueError):
            snap.recent_kql(bad)
        with pytest.raises(ValueError):
            snap.baseline_kql(bad)
    assert "asset_id == a" in snap.recent_kql("PUMP-03")
    assert "ts between" in snap.baseline_kql("PUMP-03")
