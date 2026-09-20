#!/usr/bin/env python
"""Run the agent's predictive-analysis tools locally against the live Jumpstart Eventhouse.

Uses the exact module the hosted agent uses (hosted_agent/analysis.py), so what prints here is what the agent
will compute. Handy for checking new KQL or thresholds without a Foundry deploy.

Usage: python scripts/analyze_local.py [asset_id] [signal]      (defaults: 103, vibration_mms)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "pdmops" / "foundry" / "hosted_agent"))

import analysis as an  # noqa: E402
import snapshot as snap  # noqa: E402

from pdmops.common.config import StateStore  # noqa: E402
from pdmops.common.kusto_client import EventhouseKustoClient  # noqa: E402


def main() -> None:
    asset = sys.argv[1] if len(sys.argv) > 1 else "103"
    signal = sys.argv[2] if len(sys.argv) > 2 else "vibration_mms"
    eh = StateStore().require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    c = EventhouseKustoClient(eh["queryServiceUri"], eh["kqlDatabaseName"])
    q = c.query_to_dicts
    try:
        series = q(an.series_kql(asset, signal))
        print(f"== forecast_signal({asset}, {signal}) from {len(series)} points")
        print(json.dumps(an.forecast_signal(asset, signal, series, 60), indent=1, default=str)[:1500])
        print(f"\n== time_to_limit({asset}, {signal}) default limit")
        print(json.dumps(an.time_to_limit(asset, signal, series), indent=1, default=str)[:1200])

        quality = {str(r["machine_id"]): r for r in q(an.quality_kql())}
        print(f"\n== oee_outlook({asset})")
        print(json.dumps(an.oee_outlook(asset, quality), indent=1, default=str)[:1800])

        names = {r["asset_id"]: r for r in q("pdm_asset_dim() | project asset_id, asset_name, plant_name")}
        snaps, trends = an.build_fleet_inputs(q(an.fleet_stats_kql()), q(an.fleet_series_kql()))
        print("\n== rank_fleet")
        ranked = an.rank_fleet(snaps, trends, quality, names)
        print(json.dumps(ranked["counts"]))
        for r in ranked["ranking"]:
            print(f"  {r['tier']:8} {r['asset_id']} {r['asset_name']} ({r['plant_name']}): {'; '.join(r['reasons'])[:160]}")
    finally:
        c.close()


if __name__ == "__main__":
    main()
