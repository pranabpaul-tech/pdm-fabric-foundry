#!/usr/bin/env python
"""Apply the PdM Eventhouse schema: telemetry/downtime/prediction/alert tables,
enrichment + ops functions, the telemetry update policy, materialized views,
retention/caching, and the anomaly function.

Applies artifacts/kql/[0-9]*.kql in filename order (each statement is fatal on
failure), then artifacts/kql/optional/*.kql statement by statement (failures
only warn - e.g. OneLake availability, whose syntax/region support is still
moving). Every command is create-merge / create-or-alter, so re-running is safe.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore, get_settings
from pdmops.common.kusto_client import EventhouseKustoClient
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

ARTIFACTS_KQL_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "kql"


def parse_kql_statements(text: str) -> list[str]:
    """Split a .kql file into individual management commands.

    A new statement starts at a line beginning with '.' (a dot-command); every
    following non-comment, non-blank line is appended to it until the next
    dot-command line. '//' comment lines and blank lines are always dropped -
    including ones that trail a statement - never glued onto the preceding
    statement. This is a deliberately simple heuristic, not a real KQL parser:
    it is sufficient for the hand-authored files in artifacts/kql/, not for
    arbitrary KQL scripts (a continuation line must never start with '.').
    """
    statements: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or not stripped:
            continue
        if stripped.startswith("."):
            if current:
                statements.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        statements.append("\n".join(current).strip())
    return [s for s in statements if s]


_MV_WITH_BACKFILL = re.compile(
    r"^(\.create-or-alter\s+materialized-view)\s+with\s*\(\s*backfill\s*=\s*true\s*\)\s+(\[?'?[\w-]+'?\]?)", re.I)


def adapt_for_existing_views(statement: str, existing_views: set[str]) -> str:
    """`.create-or-alter materialized-view with (backfill = true) X ...` is only valid when X does not
    exist yet: on an existing view Eventhouse rejects `backfill` ("Unsupported property in materialized
    view alter command"). Drop the option for views that already exist so the file stays re-runnable."""
    m = _MV_WITH_BACKFILL.match(statement)
    if not m:
        return statement
    name = m.group(2).strip("[]'")
    if name in existing_views:
        return statement[:m.start()] + f"{m.group(1)} {m.group(2)}" + statement[m.end():]
    return statement


def kql_files(directory: Path, pattern: str) -> list[Path]:
    return sorted(directory.glob(pattern), key=lambda p: p.name)


def run_file(client: EventhouseKustoClient, path: Path, best_effort: bool = False) -> int:
    logger.info("Applying %s%s", path.name, " (best effort)" if best_effort else "")
    failures = 0
    existing_views = {r[0] for r in client.execute_mgmt(".show materialized-views | project Name").primary_results[0].rows}
    for statement in parse_kql_statements(path.read_text(encoding="utf-8")):
        statement = adapt_for_existing_views(statement, existing_views)
        try:
            client.execute_mgmt(statement)
        except Exception as exc:  # noqa: BLE001 - best-effort files only warn
            if not best_effort:
                raise
            failures += 1
            logger.warning("Optional statement failed (continuing): %s -> %s",
                           statement.splitlines()[0][:100], str(exc)[:300])
    return failures


TARGETS = {
    # target name -> (state.json section holding the Eventhouse, KQL directory)
    "pdm": ("eventhouse", ARTIFACTS_KQL_DIR),
    "jumpstart": ("jumpstart_eventhouse", ARTIFACTS_KQL_DIR.parent / "kql-jumpstart"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default="pdm",
                        help="pdm: the hand-built Eventhouse (artifacts/kql). jumpstart: the Real-Time "
                             "Manufacturing Jumpstart's database (artifacts/kql-jumpstart, PdM layer on top).")
    parser.add_argument("--skip-optional", action="store_true", help="Do not try <dir>/optional/*.kql")
    args = parser.parse_args()

    section, kql_dir = TARGETS[args.target]
    state = StateStore()
    eventhouse = state.require(section, "queryServiceUri", "kqlDatabaseName")

    client = EventhouseKustoClient(eventhouse["queryServiceUri"], eventhouse["kqlDatabaseName"])
    optional_failures = 0
    try:
        for path in kql_files(kql_dir, "[0-9]*.kql"):
            run_file(client, path)
        if not args.skip_optional:
            for path in kql_files(kql_dir / "optional", "*.kql"):
                optional_failures += run_file(client, path, best_effort=True)
    finally:
        client.close()

    state.merge("kql_schema" if args.target == "pdm" else "kql_schema_jumpstart",
                {"applied": True, "optionalFailures": optional_failures})
    logger.info("Schema applied to '%s' (%d optional statement(s) failed).", eventhouse["kqlDatabaseName"], optional_failures)


if __name__ == "__main__":
    main()
