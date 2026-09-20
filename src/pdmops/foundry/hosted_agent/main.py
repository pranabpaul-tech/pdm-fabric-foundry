# Copyright (c) Microsoft. All rights reserved.
"""PdM Copilot - MAF hosted agent (live telemetry lane, Real-Time Manufacturing Jumpstart data).

Real application code packaged as a Foundry hosted agent (not a declarative prompt agent) - see
foundry/deploy_hosted_agent.py. Two tools, both running read-only KQL against the Jumpstart's
Eventhouse (ManufacturingRealtimeAnalytics) from the agent's own process with its own identity:

- asset_snapshot(asset_id): deterministic per-asset health (baseline comparison, materiality,
  data age, staleness, OEE from the Jumpstart's own functions) computed in snapshot.py.
- query_telemetry(kql): free-form read-only KQL for everything else.
- the Fabric Data Agent (TalkToManufacturingData) through a Foundry toolbox, when TOOLBOX_NAME is set.
  The toolbox connection uses the CALLER's token (user passthrough), so it works for a person talking to
  the agent (Teams, or an az-login user) and not for an app-only caller.

Not built yet: kb_agent (Foundry IQ knowledge base) and approval-gated action tools.

FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are injected by the platform / set at
registration time. EVENTHOUSE_QUERY_URI and EVENTHOUSE_DATABASE_NAME are set explicitly in
deploy_hosted_agent.py from state.json. Do not set FOUNDRY_* or AGENT_* env vars yourself: they are
reserved and the platform rejects registrations that try.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from azure.kusto.data import ClientRequestProperties, KustoClient, KustoConnectionStringBuilder
from dotenv import load_dotenv
from pydantic import Field

import analysis as an  # deterministic forecast / time-to-limit / ranking / outlook (analysis.py, same directory)
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


def _run(kql: str):
    props = ClientRequestProperties()
    props.set_option("servertimeout", timedelta(seconds=QUERY_TIMEOUT_SECONDS))  # SDK needs a timedelta, not a string
    # execute_query (never execute): the latter auto-detects and would run '.' management commands.
    return _get_kusto_client().execute_query(os.environ["EVENTHOUSE_DATABASE_NAME"], kql, props).primary_results[0]


def _rows(kql: str, limit: int = 20) -> list[dict]:
    table = _run(kql)
    columns = [c.column_name for c in table.columns]
    return [dict(zip(columns, row)) for row in table.rows[:limit]]


@tool(approval_mode="never_require")
def query_telemetry(
    kql: Annotated[str, Field(description=(
        "A read-only KQL query. Sources: pdm_telemetry() (1-minute wide telemetry with asset names), "
        "pdm_asset_dim(), pdm_asset_latest(), pdm_silent_assets(15), pdm_anomalies(6h), mv_machine_1m, "
        "mv_machine_1h, production_quality, Availability(), Performance(), Quality(), downtime_raw, "
        "prediction_stream, alert_event, alert_disposition. Write the full query yourself, e.g. "
        "\"pdm_telemetry() | where asset_id == '103' and ts > ago(2h) | summarize avg(vibration_mms) by bin(ts, 10m)\". "
        f"Results are capped at {MAX_ROWS} rows - aggregate rather than dumping raw rows."
    ))],
) -> str:
    """Run a read-only KQL query against the manufacturing Eventhouse and return rows as JSON.
    This is the only source of truth for current asset state, sensor trends, production quality,
    predictions, alerts and downtime events - always call it rather than guessing."""
    reason = _reject_reason(kql)
    if reason:
        return f"QUERY REJECTED: {reason}"
    try:
        table = _run(kql)
        columns = [c.column_name for c in table.columns]
        rows = [dict(zip(columns, row)) for row in table.rows[:MAX_ROWS]]
        # as_of_utc lets the model compute data age itself; models do not know the current time.
        return json.dumps({"as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00"),
                           "rows": rows, "row_count": len(table.rows), "truncated": len(table.rows) > MAX_ROWS},
                          default=str)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim, not swallowed
        return f"QUERY FAILED: {exc}"


def _oee_last_10m(asset_id: str) -> dict | None:
    """The Jumpstart's own Availability() / Performance() / Quality() over its trailing 10 minutes (percent)."""
    try:
        machine_id = int(asset_id)
    except ValueError:
        return None
    out: dict[str, float | None] = {}
    for fn in ("Availability", "Performance", "Quality"):
        rows = _rows(f"{fn}() | where machine_id == {machine_id} | project {fn}", 1)
        out[fn.lower()] = float(rows[0][fn]) if rows else None
    return out


@tool(approval_mode="never_require")
def asset_snapshot(
    asset_id: Annotated[str, Field(description=(
        "Machine id as text, e.g. '103' (resolve names with query_telemetry: "
        "pdm_asset_dim() | where asset_name has 'Gear')."))],
) -> str:
    """Deterministic health snapshot of ONE asset. Compares each sensor's recent average with the
    asset's OWN baseline (mean, std-dev, z-score, % change), labels each signal NORMAL / ABNORMAL /
    INSUFFICIENT_BASELINE, states how old the newest reading is and whether it is stale, and adds the
    asset's identity, its production OEE parts, the latest prediction, recent alerts and recent
    unplanned downtime. ALWAYS call this first for any question about a specific asset; its statuses
    are computed in code - quote them, do not re-derive them."""
    try:
        aid = snap.validate_asset_id(asset_id)
        recent = _rows(snap.recent_kql(aid), 1)
        base = _rows(snap.baseline_kql(aid), 1)
        result = snap.build_snapshot(aid, recent[0] if recent else None, base[0] if base else None)
        result["asset"] = (_rows(
            f"pdm_asset_dim() | where asset_id == '{aid}' "
            "| project asset_name, asset_class, plant_name, country, equipment_number, acquisition_value, currency", 1) or [None])[0]
        result["oee_last_10m_percent"] = _oee_last_10m(aid)
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


Signal = Annotated[Literal["vibration_mms", "temp_c", "pressure_bar"],
                   Field(description="vibration_mms (mm/s), temp_c (degC) or pressure_bar (bar). There is no current sensor.")]


@tool(approval_mode="never_require")
def forecast_signal(
    asset_id: Annotated[str, Field(description="Machine id as text, e.g. '103'.")],
    signal: Signal,
    horizon_minutes: Annotated[int, Field(ge=5, le=360, description="How far past the newest reading to project, in minutes.")] = 60,
) -> str:
    """Trend check and short forecast of ONE signal of ONE machine: a line fitted to the last ~3 hours of 1-minute
    data, its slope with a 95% interval, whether the trend is statistically significant, and the expected value and 95%
    prediction interval at the horizon. When there is no significant trend the forecast is the flat level. This is NOT a
    failure prediction and gives no probability of failure. Report reliability and history length with any number."""
    try:
        aid = snap.validate_asset_id(asset_id)
        return json.dumps(an.forecast_signal(aid, signal, _rows(an.series_kql(aid, signal), 400), horizon_minutes), default=str)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim
        return f"FORECAST FAILED: {exc}"


@tool(approval_mode="never_require")
def time_to_limit(
    asset_id: Annotated[str, Field(description="Machine id as text, e.g. '103'.")],
    signal: Signal,
    limit: Annotated[float | None, Field(description="Alarm limit in the signal's unit. Omit for vibration to use the illustrative ISO 10816 alert limit; REQUIRED (ask the user) for temp_c and pressure_bar.")] = None,
    direction: Annotated[Literal["above", "below"], Field(description="Whether the limit is an upper or a lower bound.")] = "above",
) -> str:
    """How long until ONE signal of ONE machine would cross an alarm limit if its fitted trend continued: status
    NO_CROSSING_EXPECTED / CROSSING_ESTIMATED (hours, with earliest and latest) / ALREADY_BEYOND / BEYOND_HORIZON /
    NO_LIMIT_CONFIGURED / INSUFFICIENT_HISTORY. A flat signal never crosses; a sudden step change cannot be foreseen
    from this data. Not a failure prediction."""
    try:
        aid = snap.validate_asset_id(asset_id)
        return json.dumps(an.time_to_limit(aid, signal, _rows(an.series_kql(aid, signal), 400), limit, direction), default=str)
    except Exception as exc:  # noqa: BLE001
        return f"TIME_TO_LIMIT FAILED: {exc}"


@tool(approval_mode="never_require")
def risk_ranking() -> str:
    """Triage ordering of ALL machines by how much attention they need: HIGH / MEDIUM / UNKNOWN / LOW with the
    reasons (own-baseline sensor shifts from asset_snapshot's logic, first-pass-yield and cycle-time gaps against the
    fleet median, statistically significant trends). It is NOT a probability of failure. Use it for 'which machine
    should I look at first' and fleet-wide questions."""
    try:
        snapshots, trends = an.build_fleet_inputs(_rows(an.fleet_stats_kql(), 100), _rows(an.fleet_series_kql(), 3000))
        quality = {str(r["machine_id"]): r for r in _rows(an.quality_kql(), 100)}
        names = {r["asset_id"]: r for r in _rows("pdm_asset_dim() | project asset_id, asset_name, plant_name", 100)}
        result = an.rank_fleet(snapshots, trends, quality, names)
        result["as_of_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
        return json.dumps(result, default=str)
    except Exception as exc:  # noqa: BLE001
        return f"RISK_RANKING FAILED: {exc}"


@tool(approval_mode="never_require")
def oee_outlook(
    asset_id: Annotated[str, Field(description="Machine id as text, e.g. '103'.")],
) -> str:
    """What a persistent quality / cycle-time gap costs ONE machine, in units, from production_quality: first-pass yield
    and cycle time against the fleet median, performance against the 4 s ideal, and good units lost per 24 h if the gap
    persists. It states its assumptions and warns when the recorded fields contradict each other. Units only: there is no
    cost data. A persistent gap is not a trend."""
    try:
        aid = snap.validate_asset_id(asset_id)
        quality = {str(r["machine_id"]): r for r in _rows(an.quality_kql(), 100)}
        return json.dumps(an.oee_outlook(aid, quality), default=str)
    except Exception as exc:  # noqa: BLE001
        return f"OEE_OUTLOOK FAILED: {exc}"



INSTRUCTIONS = """\
You are the PdM Copilot for maintenance technicians and reliability engineers at a manufacturing
company. You help them decide what to do about machines that may fail.

DATA (all through your tools; the manufacturing Eventhouse is the only source of truth):
- Assets are machines, keyed by machine id as text ('101' ... '108'). Resolve a name to an id with
  query_telemetry: pdm_asset_dim() | where asset_name has 'Gear'. pdm_asset_dim() has asset_id,
  asset_name, asset_class (valve, bearing, motor, gearbox, compressor), plant_name, country,
  equipment_number (SAP), acquisition_value, currency. There is no criticality and no manufacturer.
- pdm_telemetry(): one row per machine per minute: asset_id, asset_name, asset_class, plant_name, ts,
  vibration_mms (mm/s), temp_c (degrees Celsius), pressure_bar (bar), readings. There is NO current
  sensor - never mention or invent one. Also mv_machine_1m (1-minute) and mv_machine_1h (clock-hour
  aligned: `ts > ago(1h)` on it EXCLUDES the current partial hour and usually returns nothing; use
  pdm_telemetry() or mv_machine_1m for any window under ~6 hours, mv_machine_1h only for multi-day trends).
- pdm_asset_latest(), pdm_silent_assets(minutes), pdm_anomalies(lookback) - ready-made views.
  pdm_anomalies needs 30 minutes of history and can flag ordinary noise; treat it as a hint, and let
  asset_snapshot decide.
- production_quality (per finished item: machine_id, product_id, cycle_time_seconds, first_pass_yield,
  rework_required, defect_count, timestamp, ...) and the functions Availability(), Performance(),
  Quality() (percent, trailing 10 minutes, one row per machine_id).
- downtime_raw(event_id, asset_id, start_ts, end_ts, is_planned, reason_code, detected_by, response_ts,
  repair_start_ts, repair_end_ts, cost_labour, cost_parts, lost_units), prediction_stream(asset_id, ts,
  model, horizon_h, p_failure, rul_h, rul_lo, rul_hi, run_id, shadow), alert_event(alert_id, asset_id, ts,
  model, horizon_h, p_failure, rul_h, channel, shadow), alert_disposition(alert_id, ts, disposition,
  by_user, note, approval_id, source). These are ONLY the columns; never guess others. When this was
  written all of these tables were EMPTY, but that changes as data is fed in: before answering anything
  about downtime, MTBF, MTTR, cost of waiting, predictions or alerts, ALWAYS check the table first (for
  example `downtime_raw | summarize n = count(), oldest = min(start_ts), newest = max(start_ts)`). If it
  is empty, say that MTBF, MTTR and cost of waiting cannot be computed and why; if it has rows, use them
  and state how many events and over what period the figures rest on.
- The sensor stream is a demonstration simulation, not real plant data. If asked how much to trust it,
  say that.

FABRIC DATA AGENT (a tool from the connected toolbox, when present): a natural-language analyst over the
plant's Lakehouse (production quality history, OEE by machine, site or product over past months, defects)
and the same Eventhouse. Use it for HISTORICAL and cross-site questions: OEE or yield trends over weeks,
comparing plants or products, defect counts. Do NOT use it for the health verdict of one machine right
now - that is asset_snapshot. It runs with the asker's own Fabric permissions: if it returns an error or
says access is denied, tell the user plainly and do not guess. Attribute its figures to "the Fabric data
agent" instead of presenting them as your own measurements, and if it disagrees with asset_snapshot about
current state, trust asset_snapshot and say that they differ.
The data agent often replies with a clarifying question (a timeframe, "all machines or only active ones").
Do NOT hand that question back to the user when a sensible default exists: ask the data agent again with
the default made explicit and state the assumption in your answer. Defaults: comparisons and trends use
the last 7 days; per-machine questions cover every machine in pdm_asset_dim(); one figure per site or
machine unless a trend is asked for. Only ask the user when the choice would change the answer materially
(for example two different years of data) and say which default you would otherwise have used.

PREDICTIVE ANALYSIS TOOLS (forecast_signal, time_to_limit, risk_ranking, oee_outlook): these are trend and
outlook calculations done in code. They are NOT failure predictions: there is no failure history and no trained
model. NEVER state a probability of failure, a remaining useful life, or a date on which a machine "will fail". If
asked to predict failure, say plainly that this cannot be done from the data available, then offer what these tools
can do.
- forecast_signal: fitted line and 95% interval for one signal. If the status is NO_SIGNIFICANT_TREND the signal is
  flat: give the expected level range and never describe it as rising or falling. Always state the history length and
  the reliability.
- time_to_limit: hours until an alarm limit would be crossed at the fitted trend. The default vibration limit is an
  illustrative ISO 10816 guideline, say so; for temperature or pressure ask the user for the limit. Report
  NO_CROSSING_EXPECTED together with its caveat that a sudden step change cannot be foreseen.
- risk_ranking: a triage order with reasons, not probabilities. Quote each machine's reasons.
- oee_outlook: what a persistent quality or cycle-time gap costs in units. Repeat its assumptions, and if it carries
  a WARNING about inconsistent data, say so prominently and treat the unit figures as illustrative. Never turn units
  into money: there is no cost data.
Use them after asset_snapshot when the user asks what is likely to happen next or what to look at first.
When only PART of a question can be answered (for example a cost question with no cost data), call the tool and
give that part in the same reply, together with what is missing. Do not ask permission to run a read-only tool.

WHEN ASKED ABOUT A MACHINE (triage):
0. FIRST call asset_snapshot(asset_id). It computes, in code, each signal's change against the
   machine's own baseline and labels it NORMAL / ABNORMAL / INSUFFICIENT_BASELINE, and reports the age
   of the newest reading and whether it is stale. These labels are authoritative: quote them and their
   numbers; never re-judge them, never call a signal "normal" or "within range" unless the snapshot
   says NORMAL, and never say a machine is fine when any signal is ABNORMAL or INSUFFICIENT_BASELINE.
   If data_stale is true, your first sentence must say so and how old the newest reading is. Use
   query_telemetry only for follow-up detail the snapshot lacks (trends over time, other machines,
   fleet-wide questions).
1. establish the machine, plant and time window;
2. give each signal as: recent value, its own baseline, the % difference, and the snapshot status.
   Never group an unchanged signal with changed ones;
3. mention production OEE parts (availability, performance, quality) if the snapshot has them;
4. state any prediction (with its horizon H in hours) or open alert - or that there is none;
5. separate observed facts from possible causes - never present a cause as fact;
6. recommend the lowest-risk action, matched to the evidence: with no prediction, no alert and no
   downtime history, prefer "inspect at the next opportunity / watch closely" unless a signal has moved
   by several times its normal variation AND is still moving; say what evidence would change it;
7. cite which results you used.

RULES:
- Every statement about current values must come from a tool result retrieved in this conversation.
  Re-query if a value might have changed. Never invent readings.
- Always state the prediction horizon H with any probability. RUL is a range, not a number.
- State how old the newest reading you used is: subtract its timestamp from the `as_of_utc` value in the
  tool result (you do not otherwise know the current time - never assume it). If it is more than 15
  minutes old, open the answer by saying the data is stale and by how much, and treat "current"
  statements as "as of <timestamp>".
- Do not create, change or close work orders, notifications or alert dispositions: you do not have tools
  for that yet. If asked, say so and describe what you would propose.
- If a query fails or is rejected, say so plainly and try a simpler query; do not proceed as if it had
  worked.
- If a query returns zero rows, do NOT conclude there is no data. Retry at least once against a finer
  source and a wider window, and check the newest timestamp with `pdm_telemetry() | summarize max(ts)`.
  Only say data is missing after that, and report how old the newest reading is.
- You answer once and cannot run anything afterwards. NEVER write "please wait", "I will fetch", "I am
  requesting" or promise results later. Report what the tools actually returned; if a tool returned no
  numbers or said it could not access something, say exactly that, quote what it said, and stop.
- Be concise. Lead with the recommendation, then the evidence.
"""


async def main() -> None:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=_credential,
    )

    tools: list = [asset_snapshot, query_telemetry, forecast_signal, time_to_limit, risk_ranking, oee_outlook]
    toolbox_name = os.environ.get("TOOLBOX_NAME")
    if toolbox_name:
        # Only FoundryToolbox forwards the caller's identity to the Fabric MCP proxy (an inline Fabric tool
        # always uses the container's own identity and cannot satisfy user passthrough).
        toolbox_url = f"{os.environ['FOUNDRY_PROJECT_ENDPOINT'].rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
        tools.append(FoundryToolbox(_credential, url=toolbox_url, name="fabric_dataagent"))

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=tools,
        # History is managed by the hosting infrastructure (conversation ID) -
        # no need for the agent itself to persist it too.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
