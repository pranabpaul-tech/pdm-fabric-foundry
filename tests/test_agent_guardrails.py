"""The hosted agent's KQL guardrail, tested offline by loading only the pure function.

main.py imports agent_framework at module import time (installed only in the agent
image), so the guard is extracted textually rather than importing the module.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "src" / "pdmops" / "foundry" / "hosted_agent" / "main.py"


def _load_reject_reason():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_reject_reason")
    module = ast.Module(body=[fn], type_ignores=[])
    ns: dict = {}
    exec(compile(module, str(MAIN), "exec"), ns)  # noqa: S102 - our own source file
    return ns["_reject_reason"]


reject = _load_reject_reason()


def test_allows_plain_queries():
    assert reject("telemetry_enriched | where asset_id == 'PUMP-03' | take 5") is None
    assert reject("fn_anomalies(6h) | summarize count() by asset_id") is None


def test_blocks_management_commands():
    assert reject(".drop table telemetry_raw") is not None
    assert reject("   .set-or-append alert_disposition <| print 1") is not None


def test_blocks_external_data_and_plugins():
    assert reject("externaldata(x:string) ['https://evil']") is not None
    assert reject("evaluate sql_request('x','y')") is not None
    assert reject("print 1 | invoke http_request('https://x')") is not None


def test_uses_execute_query_not_execute():
    text = MAIN.read_text(encoding="utf-8")
    assert ".execute_query(" in text
    assert "_get_kusto_client().execute(" not in text  # execute() would run '.' management commands


def test_servertimeout_is_a_timedelta_not_a_string():
    # Found live: passing "00:00:60" makes azure-kusto-data raise
    # "can only concatenate str (not timedelta) to str" on every query.
    text = MAIN.read_text(encoding="utf-8")
    assert 'set_option("servertimeout", timedelta(' in text
    assert "timedelta" in text.split("_reject_reason", 1)[0]  # imported at module top


def test_tool_result_carries_server_time_for_staleness_checks():
    # Found live: without it the model claimed a 35-minute-old reading was "<15 minutes old".
    text = MAIN.read_text(encoding="utf-8")
    assert '"as_of_utc"' in text and "as_of_utc" in text.split("INSTRUCTIONS", 1)[1]


def test_deploy_preserves_the_teams_activity_route():
    # Found live: update_details() rewrote protocol_configuration with `responses` only, dropping the
    # Activity Protocol route Teams needs. The deploy must capture and restore it.
    text = (MAIN.parent.parent / "deploy_hosted_agent.py").read_text(encoding="utf-8")
    assert "prior_endpoint" in text and 'prior_protocols.get("activity")' in text
    assert text.index("prior_endpoint: dict") < text.index("project.agents.update_details(") < text.index("Restored the Activity Protocol route")
