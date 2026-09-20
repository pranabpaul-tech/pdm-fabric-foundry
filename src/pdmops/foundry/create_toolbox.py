#!/usr/bin/env python
"""Create the Foundry project connection and toolbox that let a hosted agent call the Fabric Data Agent.

  connection  fabric-dataagent-obo   category RemoteTool, authType UserEntraToken, audience Fabric
  toolbox     pdm-fabric-toolbox     one tool of type fabric_iq_preview pointing at the Data Agent's MCP endpoint

`UserEntraToken` means the call runs as the END USER who is talking to the agent (Teams passes a real
user token; an app-only caller does not work). That user needs access to the Data Agent and to the
Lakehouse / Eventhouse behind it. Use FoundryToolbox in the agent, not an inline Fabric tool: only the
toolbox forwards the caller's identity.

The connection is an ARM resource (works whether the Foundry account is public or private); the toolbox
is a Foundry data-plane call, so run this while the account is public (or from inside the VNet).
Each run creates a new toolbox version; the agent always uses the default version.

Reference: foundry-iq-v2/scripts/create_fabric_toolbox.sh.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import AI_FOUNDRY_SCOPE, AZURE_MGMT_SCOPE, get_token_provider
from pdmops.common.config import StateStore
from pdmops.common.logging_setup import setup_logging
from pdmops.foundry._rest import project_endpoint

logger = setup_logging(__name__)

CONNECTION_NAME = "fabric-dataagent-obo"
TOOLBOX_NAME = "pdm-fabric-toolbox"
CONNECTION_API = "2025-10-01-preview"


def fabric_mcp_url(workspace_id: str, data_agent_id: str) -> str:
    return f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspace_id}/dataagents/{data_agent_id}/agent"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolbox-name", default=TOOLBOX_NAME)
    args = parser.parse_args()

    state = StateStore()
    project_id = state.output("wave1", "foundryProjectId")
    account = state.output("wave1", "foundryAccountName")
    project = state.output("wave1", "foundryProjectName")
    da = state.require("data_agent", "dataAgentId", "workspaceId")
    server_url = fabric_mcp_url(da["workspaceId"], da["dataAgentId"])
    tokens = get_token_provider()

    connection_id = f"{project_id}/connections/{CONNECTION_NAME}"
    logger.info("Creating connection %s -> %s", CONNECTION_NAME, server_url)
    r = requests.put(
        f"https://management.azure.com{connection_id}?api-version={CONNECTION_API}",
        headers={"Authorization": f"Bearer {tokens.get_token(AZURE_MGMT_SCOPE)}", "Content-Type": "application/json"},
        json={"properties": {"category": "RemoteTool", "authType": "UserEntraToken",
                             "target": server_url, "audience": "https://api.fabric.microsoft.com"}},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"connection PUT failed ({r.status_code}): {r.text[:1500]}")
    logger.info("Connection ok (%s)", r.status_code)

    endpoint = project_endpoint(account, project)
    logger.info("Creating toolbox %s", args.toolbox_name)
    r = requests.post(
        f"{endpoint}/toolboxes/{args.toolbox_name}/versions",
        params={"api-version": "v1"},
        headers={"Authorization": f"Bearer {tokens.get_token(AI_FOUNDRY_SCOPE)}", "Content-Type": "application/json"},
        json={"description": "PdM Fabric Data Agent (user token passthrough)",
              "tools": [{"type": "fabric_iq_preview", "project_connection_id": connection_id,
                         "server_label": "fabric-dataagent", "server_url": server_url,
                         "require_approval": "never"}]},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"toolbox POST failed ({r.status_code}): {r.text[:1500]}")
    body = r.json() if r.content else {}
    state.merge("toolbox", {"name": args.toolbox_name, "connectionName": CONNECTION_NAME, "connectionId": connection_id,
                            "serverUrl": server_url, "version": body.get("version")})
    logger.info("Toolbox version %s created. MCP endpoint: %s/toolboxes/%s/mcp?api-version=v1",
                body.get("version"), endpoint, args.toolbox_name)


if __name__ == "__main__":
    main()
