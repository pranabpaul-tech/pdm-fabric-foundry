#!/usr/bin/env python
"""One-shot smoke test: invoke the deployed hosted agent and check whether it
used the MCP toolbox tool. Prints the raw response so risk #1 (does the
Eventhouse MCP call actually work from a VNet-injected Foundry agent through
workspace-level private link) gets a real answer instead of staying unvalidated.
"""
import json
import sys

sys.path.insert(0, "/pdm-fabric-foundry/src")

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from pdmops.common.config import StateStore
from pdmops.foundry._rest import project_endpoint

state = StateStore()
account_name = state.output("wave1", "foundryAccountName")
project_name = state.output("wave1", "foundryProjectName")
agent_name = state.require("foundry_agent", "agentName")["agentName"]

endpoint = project_endpoint(account_name, project_name)
print(f"Invoking agent '{agent_name}' at {endpoint}...")

with DefaultAzureCredential() as credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
    openai_client = project.get_openai_client(agent_name=agent_name)
    response = openai_client.responses.create(
        input="Which asset has the highest average vibration over the last hour? Query the data directly.",
    )

print("=== response.status ===")
print(getattr(response, "status", "?"))
print("=== response.error ===")
print(getattr(response, "error", None))
print("=== response.output_text ===")
print(response.output_text)
print()
print("=== output item types ===")
for item in response.output:
    print(getattr(item, "type", "?"))
print("=== full response (model_dump) ===")
try:
    print(json.dumps(response.model_dump(), indent=2, default=str)[:4000])
except Exception as e:
    print("dump failed:", e)
    print(repr(response)[:4000])
