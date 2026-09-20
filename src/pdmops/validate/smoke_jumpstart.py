#!/usr/bin/env python
"""Smoke-test the PdM layer on the Real-Time Manufacturing Jumpstart's Eventhouse.

Checks that the Jumpstart's own objects and the PdM layer (artifacts/kql-jumpstart) exist,
that telemetry is landing and fresh, and that every pdm_* function executes and returns
sane shapes. Exits non-zero on a hard failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore
from pdmops.common.kusto_client import EventhouseKustoClient
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

JUMPSTART_TABLES = {"machineraw", "mqttdataraw", "sensors_parsed", "production_quality", "machines_internal", "sites_internal"}
PDM_TABLES = {"downtime_raw", "prediction_stream", "alert_event", "alert_disposition", "approval_event", "pdm_config"}
PDM_FUNCTIONS = {"pdm_asset_dim", "pdm_telemetry", "pdm_alerts_enabled", "pdm_asset_latest", "pdm_silent_assets", "pdm_anomalies"}
PDM_VIEWS = {"mv_machine_1m", "mv_machine_1h"}


def names(client: EventhouseKustoClient, command: str) -> set[str]:
    return {r[0] for r in client.execute_mgmt(command).primary_results[0].rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freshness-minutes", type=int, default=10)
    parser.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()

    eh = StateStore().require("jumpstart_eventhouse", "queryServiceUri", "kqlDatabaseName")
    c = EventhouseKustoClient(eh["queryServiceUri"], eh["kqlDatabaseName"])
    ok = True
    try:
        for label, cmd, expected in (
            ("Jumpstart + PdM tables", ".show tables | project TableName", JUMPSTART_TABLES | PDM_TABLES),
            ("PdM functions", ".show functions | project Name", PDM_FUNCTIONS),
            ("materialized views", ".show materialized-views | project Name", PDM_VIEWS),
        ):
            missing = expected - names(c, cmd)
            if missing:
                logger.error("Missing %s: %s", label, sorted(missing))
                ok = False
            else:
                logger.info("%s present (%d)", label, len(expected))

        if ok and not args.schema_only:
            dim = c.query_to_dicts("pdm_asset_dim() | count")[0]["Count"]
            logger.info("pdm_asset_dim(): %d assets", dim)
            if dim == 0:
                logger.error("pdm_asset_dim() is empty - SAP master data not loaded (run the Jumpstart PostDeploymentNotebook)")
                ok = False
            fresh = c.query_to_dicts("sensors_parsed | summarize newest = max(timestamp), n = count() "
                                     "| extend age_min = datetime_diff('minute', now(), newest)")[0]
            logger.info("sensors_parsed: %s rows, newest reading %s min old", fresh["n"], fresh["age_min"])
            if not fresh["n"]:
                logger.error("sensors_parsed is empty - run the SimulateMachineData notebook (as its own job)")
                ok = False
            elif fresh["age_min"] > args.freshness_minutes:
                logger.error("stale: newest reading is %s min old (threshold %s)", fresh["age_min"], args.freshness_minutes)
                ok = False
            unmapped = c.query_to_dicts("pdm_telemetry() | summarize n = countif(isempty(asset_name)), total = count()")[0]
            logger.info("pdm_telemetry(): %s of %s rows without an asset name", unmapped["n"], unmapped["total"])
            for fn in ("pdm_asset_latest()", "pdm_silent_assets(15)", "pdm_anomalies(6h)", "print pdm_alerts_enabled()"):
                try:
                    c.query_to_dicts(fn if fn.startswith("print") else f"{fn} | take 5")
                    logger.info("%s executes", fn)
                except Exception as exc:  # noqa: BLE001
                    logger.error("%s failed: %s", fn, str(exc)[:200])
                    ok = False
    finally:
        c.close()
    if not ok:
        sys.exit(1)
    logger.info("smoke_jumpstart: PASS")


if __name__ == "__main__":
    main()
