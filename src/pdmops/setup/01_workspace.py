#!/usr/bin/env python
"""Create the Fabric workspace and assign it to the F8 capacity.

Run from the jumpbox, after infra Wave 1 has deployed (state.json must have a
.wave1 section) and after you're signed in with `az login` as the operator who
will later create the Operations Agent — see common/auth.assert_delegated_identity.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import assert_delegated_identity
from pdmops.common.config import StateStore, get_settings
from pdmops.common.fabric_client import FabricClient
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)


def resolve_capacity_guid(client: FabricClient, display_name: str) -> str:
    """assignToCapacity wants Fabric's own capacity GUID, not the ARM resource
    ID — hit this as a live 400 (InvalidParameter, "Error converting value ...
    to type 'System.Guid'") before adding this lookup. GET /v1/capacities
    lists them by displayName; ARM's capacity name and Fabric's displayName
    happen to match here since we never diverged them."""
    capacities = client.request("GET", "/capacities").json().get("value", [])
    match = next((c for c in capacities if c.get("displayName") == display_name), None)
    if match is None:
        raise RuntimeError(f"No Fabric capacity with displayName '{display_name}' found. "
                            f"Capacities visible to this identity: {[c.get('displayName') for c in capacities]}")
    return match["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-identity-check", action="store_true",
                         help="Skip the delegated-identity check (not recommended).")
    args = parser.parse_args()

    if not args.skip_identity_check:
        assert_delegated_identity()

    settings = get_settings()
    state = StateStore()

    client = FabricClient()
    capacity_guid = resolve_capacity_guid(client, settings.fabric_capacity_name)

    existing = state.get("workspace") or {}
    workspace_id = existing.get("workspaceId")

    if workspace_id:
        logger.info("Workspace already recorded in state.json (%s) — checking it still exists.", workspace_id)
        try:
            client.request("GET", f"/workspaces/{workspace_id}")
        except Exception:
            logger.warning("Recorded workspace no longer resolves — creating a new one.")
            workspace_id = None

    if not workspace_id:
        logger.info("Creating workspace '%s'...", settings.workspace_name)
        workspace = client.call("POST", "/workspaces", {
            "displayName": settings.workspace_name,
            "description": "PdM Copilot — real-time asset telemetry, predictive maintenance, alerts and conversational triage.",
        })
        workspace_id = workspace["id"]
        logger.info("Workspace created: %s", workspace_id)
        # Persist immediately — if assignToCapacity below fails, a re-run must
        # reuse this workspace rather than create a duplicate.
        state.merge("workspace", {"workspaceId": workspace_id, "workspaceName": settings.workspace_name})

    if existing.get("capacityAssigned"):
        logger.info("Capacity already assigned. Nothing to do.")
        return

    logger.info("Assigning workspace to capacity %s (%s)...", settings.fabric_capacity_name, capacity_guid)
    client.call("POST", f"/workspaces/{workspace_id}/assignToCapacity", {
        "capacityId": capacity_guid,
    })

    state.merge("workspace", {
        "workspaceId": workspace_id,
        "workspaceName": settings.workspace_name,
        "capacityId": capacity_guid,
        "capacityAssigned": True,
    })
    logger.info("Done. state.json['workspace'] updated.")


if __name__ == "__main__":
    main()
