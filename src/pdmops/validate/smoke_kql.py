#!/usr/bin/env python
"""Smoke-test the Eventhouse: tables exist, telemetry is landing and being
enriched by the update policy, materialized views are populated, and the ops
functions (asset latest, silent assets, anomalies) execute.

Exits non-zero on a hard failure so it can gate `azd provision` follow-up steps
and CI. Row-level checks are skipped (not failed) with --schema-only, for the
window between schema apply and the first telemetry.
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

EXPECTED_TABLES = {
    "telemetry_raw", "telemetry_enriched", "asset_dim", "downtime_raw", "prediction_stream",
    "alert_event", "alert_disposition", "approval_event", "pdm_config",
}
EXPECTED_FUNCTIONS = {
    "fn_enrich_telemetry", "fn_alerts_enabled", "fn_asset_latest", "fn_silent_assets", "fn_anomalies",
}
EXPECTED_VIEWS = {"mv_asset_1m", "mv_asset_1h"}


def check_schema(client: EventhouseKustoClient) -> bool:
    ok = True
    for label, command, expected in (
        ("tables", ".show tables | project TableName", EXPECTED_TABLES),
        ("functions", ".show functions | project Name", EXPECTED_FUNCTIONS),
        ("materialized views", ".show materialized-views | project Name", EXPECTED_VIEWS),
    ):
        rows = client.execute_mgmt(command).primary_results[0].rows
        found = {r[0] for r in rows}
        missing = expected - found
        if missing:
            logger.error("Missing %s: %s (did setup/03_kql_schema.py complete?)", label, sorted(missing))
            ok = False
        else:
            logger.info("%s present: %d expected", label, len(expected))
    return ok


def check_data(client: EventhouseKustoClient, freshness_minutes: int) -> bool:
    ok = True
    raw = client.query_to_dicts("telemetry_raw | count")[0]["Count"]
    enriched = client.query_to_dicts("telemetry_enriched | count")[0]["Count"]
    logger.info("telemetry_raw rows: %s, telemetry_enriched rows: %s", raw, enriched)
    if raw == 0:
        logger.error("telemetry_raw is empty - no telemetry is landing. Check the Jumpstart Eventstream, or run "
                     "scripts/simulate_telemetry.py for a dev environment.")
        return False
    if enriched == 0:
        logger.error("telemetry_raw has rows but telemetry_enriched is empty - the update policy is not firing. "
                     "Check `.show table telemetry_enriched policy update` and fn_enrich_telemetry().")
        ok = False

    fresh = client.query_to_dicts(
        "telemetry_enriched | summarize newest = max(ts) "
        "| extend age_min = datetime_diff('minute', now(), newest)"
    )
    if fresh and fresh[0].get("age_min") is not None:
        age = fresh[0]["age_min"]
        logger.info("Newest enriched row is %s minute(s) old", age)
        if age > freshness_minutes:
            logger.error("Freshness check failed: newest row is %s min old, threshold %s min", age, freshness_minutes)
            ok = False

    unmatched = client.query_to_dicts(
        "telemetry_enriched | where ts > ago(1h) | summarize total = count(), no_dim = countif(isempty(plant_id))"
    )
    if unmatched and unmatched[0]["total"]:
        logger.info("Rows in last hour without a matching asset_dim entry: %s of %s",
                    unmatched[0]["no_dim"], unmatched[0]["total"])

    mv_rows = client.query_to_dicts("mv_asset_1m | count")[0]["Count"]
    logger.info("mv_asset_1m rows: %s", mv_rows)
    if mv_rows == 0:
        logger.warning("mv_asset_1m is empty - materialization can lag a minute or two behind ingestion.")

    for fn in ("fn_asset_latest()", "fn_silent_assets(15)", "fn_anomalies(6h)", "fn_alerts_enabled()"):
        try:
            client.query_to_dicts(f"{fn} | take 5" if fn != "fn_alerts_enabled()" else f"print enabled = {fn}")
            logger.info("%s executes", fn)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s failed: %s", fn, str(exc)[:300])
            ok = False
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freshness-minutes", type=int, default=10,
                        help="Fail if the newest enriched row is older than this.")
    parser.add_argument("--schema-only", action="store_true", help="Only verify tables/functions/views exist.")
    args = parser.parse_args()

    state = StateStore()
    eventhouse = state.require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    client = EventhouseKustoClient(eventhouse["queryServiceUri"], eventhouse["kqlDatabaseName"])
    try:
        ok = check_schema(client)
        if ok and not args.schema_only:
            ok = check_data(client, args.freshness_minutes)
    finally:
        client.close()

    if not ok:
        sys.exit(1)
    logger.info("smoke_kql: PASS")


if __name__ == "__main__":
    main()
