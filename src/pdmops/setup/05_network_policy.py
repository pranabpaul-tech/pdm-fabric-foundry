#!/usr/bin/env python
"""Deny public access to the Fabric workspace, once workspace-level private link
(infra/wave2-fabric-privatelink.bicep) is deployed and verified reachable.

Run this LAST among the Fabric setup steps, and only with --confirm. Per the
access table in Microsoft's own private-links docs: once tenant-level public
access is also restricted, a client connected only via workspace-level private
link cannot call this same API to undo the change — you'd need a tenant-level
private link connection (or a public-access client, if tenant public access is
still allowed) to run --undo. Verify DNS resolves privately (see
validate/network_check.py) BEFORE running --confirm, not after.

API: PUT /v1/workspaces/{id}/networking/communicationPolicy
     https://learn.microsoft.com/fabric/security/security-workspace-level-private-links-set-up
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import assert_delegated_identity
from pdmops.common.config import StateStore
from pdmops.common.fabric_client import FabricClient
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--confirm", action="store_true", help="Deny public access to the workspace.")
    action.add_argument("--undo", action="store_true", help="Allow public access again.")
    action.add_argument("--status", action="store_true", help="Print the current policy and exit.")
    args = parser.parse_args()

    assert_delegated_identity()

    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")
    client = FabricClient()
    path = f"/workspaces/{workspace_id}/networking/communicationPolicy"

    if args.status:
        current = client.request("GET", path).json()
        logger.info("Current policy: %s", current)
        return

    if args.confirm:
        logger.warning(
            "About to deny public access to workspace %s. Confirm you've already verified "
            "private DNS resolution from the jumpbox (validate/network_check.py) — this can "
            "take up to 30 minutes to take effect and, once combined with tenant-level "
            "public restrictions, may not be reversible from a workspace-PL-only client.",
            workspace_id,
        )
        client.call("PUT", path, {"inbound": {"publicAccessRules": {"defaultAction": "Deny"}}})
        state.merge("workspace", {"publicAccessDenied": True})
        logger.info("Done. Public access denied — re-run validate/smoke_kql.py and "
                    "validate/network_check.py to confirm nothing broke.")
    elif args.undo:
        client.call("PUT", path, {"inbound": {"publicAccessRules": {"defaultAction": "Allow"}}})
        state.merge("workspace", {"publicAccessDenied": False})
        logger.info("Done. Public access allowed again.")


if __name__ == "__main__":
    main()
