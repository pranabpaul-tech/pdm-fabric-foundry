"""Offline checks on the KQL artifacts - no Azure needed.

These catch the mistakes that otherwise only show up mid-deployment: a command
the statement splitter would glue together, a table the smoke test expects but
the schema never creates, or a stray non-management line in a *.kql file that
the applier would silently drop.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KQL_DIR = ROOT / "artifacts" / "kql"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


schema = _load("kql_schema", "src/pdmops/setup/03_kql_schema.py")
smoke = _load("smoke_kql", "src/pdmops/validate/smoke_kql.py")


def _applied_files() -> list[Path]:
    return sorted(KQL_DIR.glob("[0-9]*.kql")) + sorted((KQL_DIR / "optional").glob("*.kql"))


def test_parser_splits_dot_commands_and_drops_comments():
    text = "// c\n.create-merge table a (x:int)\n\n// mid\n.create-merge table b (y:int,\n  z:int)\n"
    assert schema.parse_kql_statements(text) == [
        ".create-merge table a (x:int)",
        ".create-merge table b (y:int,\n  z:int)",
    ]


def test_every_applied_file_yields_only_management_commands():
    for path in _applied_files():
        text = path.read_text(encoding="utf-8")
        statements = schema.parse_kql_statements(text)
        assert statements, f"{path.name} produced no statements"
        assert all(s.startswith(".") for s in statements), path.name
        # A non-comment line before the first dot-command would be silently dropped by the applier.
        first_code = next(l for l in text.splitlines() if l.strip() and not l.strip().startswith("//"))
        assert first_code.strip().startswith("."), f"{path.name}: leading non-management line: {first_code!r}"


def test_no_command_is_idempotency_hostile():
    forbidden = re.compile(r"^\.(create|drop|delete)\s+(table|function|materialized-view)\b", re.I)
    for path in _applied_files():
        for statement in schema.parse_kql_statements(path.read_text(encoding="utf-8")):
            assert not forbidden.match(statement), f"{path.name}: use create-merge / create-or-alter: {statement[:60]}"


def test_smoke_test_expectations_match_the_schema():
    text = "\n".join(p.read_text(encoding="utf-8") for p in _applied_files())
    for table in smoke.EXPECTED_TABLES:
        assert re.search(rf"\.create-merge table {table}\b", text), f"schema never creates table {table}"
    for fn in smoke.EXPECTED_FUNCTIONS:
        assert re.search(rf"\.create-or-alter function[^\n]*\b{fn}\(", text), f"schema never creates function {fn}"
    for view in smoke.EXPECTED_VIEWS:
        assert re.search(rf"\.create-or-alter materialized-view[^\n]*\b{view}\b", text), f"schema never creates view {view}"


def test_update_policy_targets_existing_table_and_function():
    text = (KQL_DIR / "030_update_policies.kql").read_text(encoding="utf-8")
    assert "policy update" in text and "telemetry_enriched" in text
    assert '"Source": "telemetry_raw"' in text and "fn_enrich_telemetry()" in text
    assert '"IsTransactional": false' in text  # enrichment failure must never block raw ingestion


def test_backfill_option_is_dropped_for_existing_materialized_views():
    # Found live: re-running `.create-or-alter materialized-view with (backfill = true) X` on an existing X
    # is rejected ("Unsupported property in materialized view alter command").
    stmt = ".create-or-alter materialized-view with (backfill = true) mv_a on table t {\n t | summarize count() by x\n}"
    assert schema.adapt_for_existing_views(stmt, {"mv_a"}).startswith(".create-or-alter materialized-view mv_a on table t")
    assert "backfill" not in schema.adapt_for_existing_views(stmt, {"mv_a"})
    assert schema.adapt_for_existing_views(stmt, set()) == stmt                # new view keeps backfill
    assert schema.adapt_for_existing_views(".create-merge table x (a:int)", {"mv_a"}) == ".create-merge table x (a:int)"
