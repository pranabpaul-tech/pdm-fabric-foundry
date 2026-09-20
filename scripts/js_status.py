#!/usr/bin/env python
"""Print the status of the Jumpstart PostDeploymentNotebook job and row counts of the
Jumpstart Eventhouse tables, one line. Used to watch a running install.

Usage: python scripts/js_status.py [--cancel]
--cancel stops the PostDeploymentNotebook job (it ends with a 2-hour simulator loop).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdmops.common.config import StateStore  # noqa: E402
from pdmops.common.fabric_client import FabricClient  # noqa: E402
from pdmops.common.kusto_client import EventhouseKustoClient  # noqa: E402


def main() -> None:
    state = StateStore()
    js = state.require("jumpstart_workspace", "workspaceId", "postDeploymentNotebookId", "postDeploymentJobId")
    eh = state.require("jumpstart_eventhouse", "queryServiceUri", "kqlDatabaseName")
    fc = FabricClient()
    base = f"/workspaces/{js['workspaceId']}/items/{js['postDeploymentNotebookId']}/jobs/instances/{js['postDeploymentJobId']}"

    if "--cancel" in sys.argv:
        fc.request("POST", f"{base}/cancel", {})
        print("cancel requested")
        return

    job = fc.request("GET", base).json()
    status = job.get("status")
    started = (job.get("startTimeUtc") or "")[:19]
    err = (job.get("failureReason") or {}).get("message", "")
    counts = "n/a"
    try:
        c = EventhouseKustoClient(eh["queryServiceUri"], eh["kqlDatabaseName"])
        rows = c.query_to_dicts(
            "union withsource=t machineraw, sensors_parsed, production_quality, machines_internal, sites_internal "
            "| summarize n = count() by t | order by t asc")
        counts = ", ".join(f"{r['t']}={r['n']}" for r in rows)
        c.close()
    except Exception as exc:  # noqa: BLE001
        counts = f"kql error: {str(exc)[:80]}"
    print(f"job={status} started={started} | {counts}" + (f" | ERROR: {err[:200]}" if err else ""))


if __name__ == "__main__":
    main()
