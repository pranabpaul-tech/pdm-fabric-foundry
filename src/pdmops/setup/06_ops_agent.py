#!/usr/bin/env python
"""Create the "PdM Operations Monitor" Operations Agent from
artifacts/ops-agent/OperationsAgentV1.json, or capture a portal-authored one.

MUST run under the operator's own delegated identity, not a service principal
— the Operations Agent inherits its creator's identity and runs actions as
them. This script fails fast (assert_delegated_identity) rather than silently
creating an agent nobody can trace.

Not available in East US, not in sovereign clouds, not in CMK-encrypted
workspaces — if creation fails with a region/policy error, that's most likely
why, not a bug in this script.

Workflow:
  1. Author once in the portal (instructions, KQL data source, Generate
     Playbook, review the generated per-rule KQL in Query Insights).
  2. python 06_ops_agent.py --capture <opsAgentId>
  3. Edit artifacts/ops-agent/OperationsAgentV1.json locally if needed.
  4. python 06_ops_agent.py --update <opsAgentId>   (an agent that already
     exists — the common case) or --apply (create a brand new one from the
     file, e.g. in a fresh environment).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import assert_delegated_identity
from pdmops.common.config import StateStore, get_settings
from pdmops.common.fabric_client import FabricClient, decode_definition_part, definition_part
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

DEFINITION_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "ops-agent" / "OperationsAgentV1.json"
# Confirmed live against a portal-authored agent: Fabric's own definition part
# for this item type is "Configurations.json", not "OperationsAgentV1.json"
# (that name only ever existed in our own placeholder guess).
DEFINITION_ITEM_PATH = "Configurations.json"


def capture(client: FabricClient, workspace_id: str, ops_agent_id: str) -> None:
    logger.info("Fetching definition for Operations Agent %s...", ops_agent_id)
    result = client.call("POST", f"/workspaces/{workspace_id}/operationsAgents/{ops_agent_id}/getDefinition"
                                  f"?format=OperationsAgentV1")
    parts = result.get("definition", {}).get("parts", [])
    part = next((p for p in parts if p["path"] == DEFINITION_ITEM_PATH), None)
    if part is None:
        raise RuntimeError(f"getDefinition response had no '{DEFINITION_ITEM_PATH}' part. Parts present: "
                            f"{[p.get('path') for p in parts]}")
    decoded = decode_definition_part(part)
    DEFINITION_PATH.write_text(json.dumps(decoded, indent=2), encoding="utf-8")
    logger.info("Captured real definition -> %s. Review it, then commit it.", DEFINITION_PATH)


def update(client: FabricClient, workspace_id: str, ops_agent_id: str, state: StateStore) -> None:
    """Push the local instructions/dataSources/actions/messageDestination onto
    an EXISTING (portal-created) agent. Merges into its current live
    definition rather than overwriting it wholesale — Fabric stores a
    compiled `playbook` (from clicking Generate Playbook) and `shouldRun`
    alongside the authored config, and this repo has no way to reconstruct
    those if they're naively clobbered. Confirmed live: pushing a definition
    with an empty playbook gets rejected outright once shouldRun is true, and
    can silently reset the agent to Inactive with the real playbook cleared."""
    raw = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    if raw.get("_placeholder"):
        raise RuntimeError(f"{DEFINITION_PATH} is still the placeholder shape — nothing real to push.")
    _retarget_kusto_data_source(raw.get("configuration", {}), state)

    result = client.call("POST", f"/workspaces/{workspace_id}/operationsAgents/{ops_agent_id}/getDefinition"
                                  f"?format=OperationsAgentV1")
    parts = result.get("definition", {}).get("parts", [])
    platform_part = next(p for p in parts if p["path"] == ".platform")
    config_part = next(p for p in parts if p["path"] == DEFINITION_ITEM_PATH)
    live_cfg = decode_definition_part(config_part)

    for key in ("instructions", "dataSources", "actions", "messageDestination"):
        if key in raw.get("configuration", {}):
            live_cfg["configuration"][key] = raw["configuration"][key]
    # live_cfg["playbook"] and live_cfg["shouldRun"] are left exactly as
    # fetched — those belong to the portal's Generate Playbook / Start actions.

    client.call("POST", f"/workspaces/{workspace_id}/operationsAgents/{ops_agent_id}/updateDefinition", {
        "definition": {
            "parts": [
                definition_part(DEFINITION_ITEM_PATH, live_cfg),
                {"path": ".platform", "payload": platform_part["payload"], "payloadType": "InlineBase64"},
            ],
        },
    })
    state.merge("ops_agent", {"opsAgentId": ops_agent_id, "instructionsSet": True})
    logger.info("Done. Pushed instructions/dataSources to Operations Agent %s.", ops_agent_id)
    logger.info("If the instructions changed meaningfully, re-run Generate Playbook in the portal.")


def _retarget_kusto_data_source(config: dict, state: StateStore) -> None:
    """The captured definition's dataSources block hardcodes the
    workspaceId/kqlDatabaseId of whichever Eventhouse was live at capture
    time — those are per-deployment IDs, not part of the agent's authored
    config, and go stale the moment that Eventhouse is ever recreated (same
    class of problem as 04_eventstream.py's _retarget_eventhouse_destinations,
    which this mirrors). Rewrite every KustoDatabase data source in place
    to point at the current state.json values on every apply/update.
    """
    eventhouse = state.require("eventhouse", "kqlDatabaseId")
    workspace_id = state.output("workspace", "workspaceId")
    kql_database_id = eventhouse["kqlDatabaseId"]

    data_sources = config.get("dataSources", {})
    retargeted = {}
    for source in data_sources.values():
        if source.get("type") != "KustoDatabase":
            retargeted[source["id"]] = source
            continue
        retargeted[kql_database_id] = {
            "id": kql_database_id,
            "type": "KustoDatabase",
            "workspaceId": workspace_id,
        }
    config["dataSources"] = retargeted


def apply(client: FabricClient, workspace_id: str, state: StateStore) -> None:
    raw = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
    if raw.get("_placeholder"):
        raise RuntimeError(
            f"{DEFINITION_PATH} is still the placeholder shape. Author the Operations Agent once in "
            f"the portal and run `python 06_ops_agent.py --capture <opsAgentId>` first — see this "
            f"script's module docstring."
        )
    clean = {k: v for k, v in raw.items() if not k.startswith("_")}
    _retarget_kusto_data_source(clean.get("configuration", {}), state)

    settings = get_settings()
    existing = state.get("ops_agent")
    if existing and existing.get("opsAgentId"):
        logger.info("Operations Agent already recorded in state.json — nothing to do.")
        return

    logger.info("Creating Operations Agent '%s' from %s...", settings.ops_agent_name, DEFINITION_PATH)
    result = client.call("POST", f"/workspaces/{workspace_id}/operationsAgents", {
        "displayName": settings.ops_agent_name,
        "definition": {
            "parts": [definition_part(DEFINITION_ITEM_PATH, clean)],
        },
    })
    ops_agent_id = result["id"]
    state.merge("ops_agent", {
        "opsAgentId": ops_agent_id,
        "opsAgentName": settings.ops_agent_name,
    })
    logger.info("Done. state.json['ops_agent'] updated. opsAgentId=%s", ops_agent_id)
    logger.info("Next: install the Fabric Operations Agent Teams app, set the PdM Operations "
                "channel as recipient, wire the Power Automate action, and start the agent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="Create a brand new Operations Agent from the local file.")
    mode.add_argument("--capture", metavar="OPS_AGENT_ID", help="Pull a portal-authored agent's real definition into the local file.")
    mode.add_argument("--update", metavar="OPS_AGENT_ID", help="Push the local file's instructions/dataSources/actions onto an existing agent.")
    parser.add_argument("--skip-identity-check", action="store_true")
    args = parser.parse_args()

    if not args.skip_identity_check:
        assert_delegated_identity()

    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")
    client = FabricClient()

    if args.capture:
        capture(client, workspace_id, args.capture)
    elif args.update:
        update(client, workspace_id, args.update, state)
    else:
        apply(client, workspace_id, state)


if __name__ == "__main__":
    main()
