#!/usr/bin/env python
"""Dev-only synthetic telemetry + asset dimension, written straight into the
Eventhouse with `.ingest inline` so the whole path can be proven WITHOUT the
Real-Time Manufacturing Jumpstart or any MQTT source:

    telemetry_raw --(update policy fn_enrich_telemetry)--> telemetry_enriched
                  --(materialized views)--> mv_asset_1m / mv_asset_1h --> fn_anomalies

It seeds `asset_dim` (idempotently) and writes N minutes of readings for a few
assets. One asset ("PUMP-03" by default) gets a drifting vibration signal near
the end so fn_anomalies() has something real to find.

`.ingest inline` is fine for a few thousand rows; it is NOT a load-test tool and
must never point at a production Eventhouse (it refuses unless --i-know-this-is-dev).

Usage:
    python scripts/simulate_telemetry.py --i-know-this-is-dev [--minutes 180] [--fault-asset PUMP-03]
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdmops.common.config import StateStore  # noqa: E402
from pdmops.common.kusto_client import EventhouseKustoClient  # noqa: E402
from pdmops.common.logging_setup import setup_logging  # noqa: E402

logger = setup_logging(__name__)

ASSETS = [
    # asset_id, name, plant, line, class, criticality
    ("PUMP-01", "Coolant pump 1", "PLANT-A", "LINE-1", "pump", 2),
    ("PUMP-03", "Coolant pump 3", "PLANT-A", "LINE-1", "pump", 3),
    ("COMP-02", "Air compressor 2", "PLANT-A", "LINE-2", "compressor", 3),
    ("MOTOR-07", "Spindle motor 7", "PLANT-B", "LINE-1", "motor", 2),
]
BATCH = 500


def build_telemetry(minutes: int, fault_asset: str | None, seed: int) -> list[str]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    rows: list[str] = []
    for asset_id, *_ in ASSETS:
        cycles = 100000
        for i in range(minutes):
            ts = now - timedelta(minutes=minutes - i)
            daily = math.sin(2 * math.pi * (ts.hour * 60 + ts.minute) / 1440)
            vib = 2.0 + 0.15 * daily + rng.gauss(0, 0.05)
            temp = 62 + 2.0 * daily + rng.gauss(0, 0.4)
            cur = 11.0 + 0.4 * daily + rng.gauss(0, 0.1)
            press = 6.0 + rng.gauss(0, 0.05)
            if fault_asset == asset_id and i > minutes * 0.75:
                drift = (i - minutes * 0.75) / (minutes * 0.25)   # 0 -> 1 over the last quarter
                vib += 2.5 * drift ** 2
                temp += 6.0 * drift
            cycles += rng.randint(8, 14)
            rows.append(f"{asset_id},{ts.strftime('%Y-%m-%dT%H:%M:%SZ')},{vib:.4f},{temp:.3f},{cur:.3f},{press:.3f},{cycles},{{}}")
    return rows


def ingest(client: EventhouseKustoClient, table: str, rows: list[str]) -> None:
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        client.execute_mgmt(f".ingest inline into table {table} <|\n" + "\n".join(chunk))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=180)
    parser.add_argument("--fault-asset", default="PUMP-03")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--i-know-this-is-dev", action="store_true", required=True)
    args = parser.parse_args()

    eventhouse = StateStore().require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    client = EventhouseKustoClient(eventhouse["queryServiceUri"], eventhouse["kqlDatabaseName"])
    try:
        # asset_dim first, so the update policy can enrich the telemetry that follows.
        # replace, not append: re-running must not duplicate dimension rows.
        dim_csv = "\n".join(f"{a},{n},{p},{l},{c},{k}" for a, n, p, l, c, k in ASSETS)
        client.execute_mgmt(
            ".set-or-replace asset_dim <|\n"
            "datatable(asset_id:string, asset_name:string, plant_id:string, line_id:string, asset_class:string, criticality:int)\n"
            "[\n" + ",\n".join(f"'{a}','{n}','{p}','{l}','{c}',{k}" for a, n, p, l, c, k in ASSETS) + "\n]"
        )
        logger.info("asset_dim seeded with %d assets", len(ASSETS))

        rows = build_telemetry(args.minutes, args.fault_asset or None, args.seed)
        logger.info("Ingesting %d telemetry rows (%d min x %d assets)...", len(rows), args.minutes, len(ASSETS))
        ingest(client, "telemetry_raw", rows)
        logger.info("Done. Give the update policy + materialized views a minute, then run "
                    "src/pdmops/validate/smoke_kql.py and: fn_anomalies(3h) | where asset_id == '%s'", args.fault_asset)
    finally:
        client.close()


if __name__ == "__main__":
    main()
