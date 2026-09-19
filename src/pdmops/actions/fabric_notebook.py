#!/usr/bin/env python
"""Action tool: run a Fabric notebook on demand for deeper analysis / incident
state updates. Gated on approval.require_approved, same as power_automate.py.

Uses the Fabric Job Scheduler API:
POST /v1/workspaces/{workspaceId}/items/{notebookId}/jobs/instances?jobType=RunNotebook
which is a standard Fabric long-running operation — FabricClient.call() polls
it to completion the same way every setup/ script does.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore
from pdmops.common.fabric_client import FabricClient
from pdmops.common.logging_setup import setup_logging
from .approval import require_approved
from .incident_store import RecordStore

logger = setup_logging(__name__)


def run_notebook(approval_id: str, notebook_id: str, parameters: dict | None = None) -> dict[str, Any]:
    approval = require_approved(approval_id)

    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")
    client = FabricClient()

    body: dict[str, Any] = {}
    if parameters:
        body = {"executionData": {"parameters": {
            k: {"value": v, "type": "string"} for k, v in parameters.items()
        }}}

    result: dict[str, Any]
    try:
        job = client.call(
            "POST",
            f"/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances",
            body,
        )
        result = {"success": True, "job": job}
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim to the caller, not swallowed
        result = {"success": False, "error": str(exc)}

    RecordStore("actions").create({
        "actionType": "fabric_notebook.run_notebook",
        "approvalId": approval_id,
        "notebookId": notebook_id,
        "parameters": parameters or {},
        "approvedBy": approval.get("decidedBy"),
        "result": result,
    })

    if not result["success"]:
        logger.error("Notebook run failed: %s", result)
    return result
