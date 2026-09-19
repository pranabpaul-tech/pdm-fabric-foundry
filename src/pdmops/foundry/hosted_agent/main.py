# Copyright (c) Microsoft. All rights reserved.
"""PdM Copilot - MAF hosted agent (v0: live telemetry lane).

Real application code packaged as a Foundry hosted agent (not a declarative
prompt agent) - see foundry/deploy_hosted_agent.py. This first cut has ONE
tool, `query_telemetry`, which runs read-only KQL against the Eventhouse from
the agent's own process with its own granted identity (no MCP, no Toolbox).

Roadmap (see DEPLOYMENT_PLAN_v2.md section 9): lakehouse_agent (T-SQL on the
gold tables), kb_agent (Foundry IQ knowledge base), the Fabric Data Agent
toolbox (user OBO), and the approval-gated action tools - each added as an
in-process tool of this same orchestrator.

FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are injected by the
platform / set at registration time. EVENTHOUSE_QUERY_URI and
EVENTHOUSE_DATABASE_NAME are set explicitly in deploy_hosted_agent.py from
state.json. Do not set FOUNDRY_* or AGENT_* env vars yourself: they are
reserved and the platform rejects registrations that try.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from azure.kusto.data import ClientRequestProperties, KustoClient, KustoConnectionStringBuilder
from dotenv import load_dotenv
from pydantic import Field

import snapshot as snap  # deterministic baseline/staleness logic (snapshot.py, same directory)

load_dotenv()

_credential = DefaultAzureCredential()
_kusto_client: KustoClient | None = None

MAX_ROWS = 200
QUERY_TIMEOUT_SECONDS = 60
# The real control is the identity grant (database viewer only; ingestor is granted on
# specific tables by the approval-gated action tools, never to this tool).


def _get_kusto_client() -> KustoClient:
    global _kusto_client
    if _kusto_client is None:
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            connection_string=os.environ["EVENTHOUSE_QUERY_URI"],
            credential=_credential,
        )
        _kusto_client = KustoClient(kcsb)
    return _kusto_client


def _reject_reason(kql: str) -> str | None:
    """Cheap guardrails in front of the real control (the identity is only granted
    viewer on the database). Blocks management commands and obvious escapes."""
    stripped = kql.strip()
    if stripped.startswith("."):
        return "management commands (leading '.') are not allowed; this tool is read-only"
    lowered = stripped.lower()
    for banned in ("externaldata", "evaluate ", "http_request", "sql_request", "cosmosdb_request", ".ingest"):
        if banned in lowered:
            return f"'{banned.strip()}' is not allowed in this tool"
    return None


@tool(approval_mode="never_require")
def query_telemetry(
    kql: Annotated[str, Field(description=(
        "A read-only KQL query. Useful sources: telemetry_enriched, mv_asset_1m, mv_asset_1h, downtime_raw, "
        "prediction_stream, alert_event, alert_disposition, and the functions fn_asset_latest(), "
        "fn_silent_assets(15), fn_anomalies(6h). Write the full query yourself, e.g. "
        "\"telemetry_enriched | where asset_id == 'PUMP-03' and ts > ago(2h) | summarize avg(vibration_rms) by bin(ts, 10m)\". "
        f"Results are capped at {MAX_ROWS} rows - aggregate rather than dumping raw rows."
    ))],
) -> str:
    """Run a read-only KQL query against the PdM Eventhouse and return rows as JSON.
    This is the only source of truth for current asset state, sensor trends,
    predictions, alerts and downtime events - always call it rather than guessing."""
    reason = _reject_reason(kql)
    if reason:
        return f"QUERY REJECTED: {reason}"
    try:
        props = ClientRequestProperties()
        props.set_option("servertimeout", timedelta(seconds=QUERY_TIMEOUT_SECONDS))  # SDK needs a timedelta, not a string
        # execute_query (never execute): the latter auto-detects and would run '.' management commands.
        response = _get_kusto_client().execute_query(os.environ["EVENTHOUSE_DATABASE_NAME"], kql, props)
        table = response.primary_results[0]
        columns = [c.column_name for c in table.columns]
        rows = [dict(zip(columns, row)) for row in table.rows[:MAX_ROWS]]
        truncated = len(table.rows) > MAX_ROWS
        # as_utc_now lets the model compute data age itself; models do not know the current time.
        return json.dumps({"as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00"),
                           "rows": rows, "row_count": len(table.rows), "truncated": truncated}, default=str)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim, not swallowed
        return f"QUERY FAILED: {exc}"


def _rows(kql: str, limit: int = 20) -> list[dict]:
    props = ClientRequestProperties()
    props.set_option("servertimeout", timedelta(seconds=QUERY_TIMEOUT_SECONDS))
    table = _get_kusto_client().execute_query(os.environ["EVENTHOUSE_DATABASE_NAME"], kql, props).primary_results[0]
    columns = [c.column_name for c in table.columns]
    return [dict(zip(columns, row)) for row in table.rows[:limit]]


@tool(approval_mode="never_require")
def asset_snapshot(
    asset_id: Annotated[str, Field(description="Exact asset id, e.g. PUMP-03 (see asset_dim / fn_asset_latest()).")],
) -> str:
    """Deterministic health snapshot of ONE asset. Compares each sensor's recent average with the
    asset's OWN baseline (mean, std-dev, z-score, % change), labels each signal NORMAL / ABNORMAL /
    INSUFFICIENT_BASELINE, states how old the newest reading is and whether it is stale, and adds
    the latest prediction, recent alerts and recent unplanned downtime. ALWAYS call this first for
    any question about a specific asset; its statuses are computed in code - quote them, do not
    re-derive them."""
    try:
        aid = snap.validate_asset_id(asset_id)
        recent = _rows(snap.recent_kql(aid), 1)
        base = _rows(snap.baseline_kql(aid), 1)
        result = snap.build_snapshot(aid, recent[0] if recent else None, base[0] if base else None)
        result["latest_prediction"] = _rows(
            f"prediction_stream | where asset_id == '{aid}' | top 1 by ts desc "
            "| project ts, model, horizon_h, p_failure, rul_h, rul_lo, rul_hi, shadow", 1) or None
        result["alerts_last_7d"] = _rows(
            f"alert_event | where asset_id == '{aid}' and ts > ago(7d) | top 5 by ts desc "
            "| project alert_id, ts, p_failure, rul_h, channel, shadow", 5)
        result["unplanned_downtime_last_180d"] = _rows(
            f"downtime_raw | where asset_id == '{aid}' and is_planned == false and start_ts > ago(180d) "
            "| top 5 by start_ts desc | project start_ts, end_ts, reason_code, cost_labour, cost_parts, lost_units", 5)
        return json.dumps(result, default=str)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim
        return f"SNAPSHOT FAILED: {exc}"


INSTRUCTIONS = f"""\
You are the PdM Copilot for maintenance technicians and reliability engineers at a
manufacturing site. You help them decide what to do about assets that may fail.

DATA (all via the query_telemetry tool; the Eventhouse is the only source of truth
for current state):
- telemetry_enriched(asset_id, ts, vibration_rms, temp_c, current_a, pressure_bar,
  cycle_count, plant_id, line_id, asset_class, criticality) - raw sensor readings.
- mv_asset_1m / mv_asset_1h(asset_id, ts, avg_vib, max_vib, avg_temp, avg_cur, avg_press,
  cycle_max, n) - per-asset aggregates. mv_asset_1m is 1-minute bins; mv_asset_1h bins are
  clock-hour aligned, so `ts > ago(1h)` on it EXCLUDES the current partial hour and usually
  returns nothing. Use mv_asset_1m (or telemetry_enriched) for any window under ~6 hours, and
  mv_asset_1h only for multi-day trends.
- downtime_raw(event_id, asset_id, start_ts, end_ts, is_planned, reason_code, detected_by,
  response_ts, repair_start_ts, repair_end_ts, cost_labour, cost_parts, lost_units) -
  labelled stop events, the basis for MTBF / MTTR.
- prediction_stream(asset_id, ts, model, horizon_h, p_failure, rul_h, rul_lo, rul_hi,
  run_id, shadow) - model outputs. Rows with shadow == true are logged for evaluation
  only and have NOT been acted on; say so whenever you cite one.
- alert_event(alert_id, asset_id, ts, model, horizon_h, p_failure, rul_h, channel, shadow) -
  alerts raised. alert_disposition(alert_id, ts, disposition, by_user, note, approval_id, source) -
  what the technician decided (confirmed_tp | false_alarm | already_known | not_actioned).
  These are the ONLY columns; never guess others.
- fn_asset_latest(), fn_silent_assets(minutes), fn_anomalies(lookback) - ready-made views.

WHEN ASKED ABOUT AN ASSET (triage):
0. FIRST call asset_snapshot(asset_id). It computes, in code, each signal's change against the
   asset's own baseline and labels it NORMAL / ABNORMAL / INSUFFICIENT_BASELINE, and reports the
   age of the newest reading and whether it is stale. These labels are authoritative: quote them
   and their numbers; never re-judge them, never call a signal "normal" or "within range"
   unless the snapshot says NORMAL, and never say an asset is fine when any signal is ABNORMAL
   or INSUFFICIENT_BASELINE. If data_stale is true, your first sentence must say so and how old
   the newest reading is. Use query_telemetry only for follow-up detail the snapshot lacks
   (trends over time, other assets, fleet-wide questions).
1. establish the asset, plant/line and the time window;
2. retrieve its recent trend and compare with its own longer baseline;
3. retrieve any prediction (state the horizon H in hours, the probability, and the
   RUL with its confidence band) and any open alert;
4. retrieve similar past unplanned stops for the asset class from downtime_raw;
5. quantify: for every signal you call abnormal, give the recent value, its own baseline
   and the % difference. Call a signal abnormal only if the change is clearly larger than its
   normal variation (a shift of a few percent is normal - say "unchanged"). Never group an
   unchanged signal with changed ones. Then separate observed facts from possible causes -
   never present a cause as fact;
6. recommend the lowest-risk action and say what waiting costs, using only figures
   you retrieved (downtime cost_labour/cost_parts/lost_units); say when you lack them;
7. cite the query results you used.

RULES:
- Every statement about current values must come from a tool result retrieved in this
  conversation. Re-query if the value might have changed. Never invent readings.
- Always state the prediction horizon H with any probability. RUL is a range, not a number.
- Always state how old the newest reading you used is: subtract its timestamp from the
  `as_of_utc` value in the tool result (you do not otherwise know the current time - never
  assume it). If it is more than 15 minutes old, open the answer by saying the data is stale
  and by how much, and treat "current" statements as "as of <timestamp>".
- Match the recommendation to the evidence: with no prediction, no alert and no downtime
  history, prefer "inspect at the next opportunity / watch closely" unless a signal has moved
  by several times its normal variation AND is still moving; say what evidence would change it.
- Do not create, change or close work orders, notifications or alert dispositions: you
  do not have tools for that yet. If asked, say so and describe what you would propose.
- If a query fails or is rejected, say so plainly and try a simpler query; do not
  proceed as if it had worked.
- If a query returns zero rows, do NOT conclude there is no data. Retry at least once against
  a finer source (mv_asset_1m, then telemetry_enriched) and a wider window (e.g. ago(6h)) and
  check the newest timestamp with `telemetry_enriched | summarize max(ts)`. Only say data is
  missing after that, and report how old the newest reading is.
- Be concise. Lead with the recommendation, then the evidence.
"""


async def main() -> None:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=_credential,
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[asset_snapshot, query_telemetry],
        # History is managed by the hosting infrastructure (conversation ID) -
        # no need for the agent itself to persist it too.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
