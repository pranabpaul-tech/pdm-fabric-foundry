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
