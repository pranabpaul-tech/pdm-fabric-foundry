#!/usr/bin/env python
"""Ask the deployed hosted agent one or more questions and print, for each: the tool
calls it made (the KQL it ran), a short view of each tool result, and the final answer.

Usage: python scripts/ask_agent.py "question one" ["question two" ...]
Run from the repo root with `az login` done (uses DefaultAzureCredential).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azure.ai.projects import AIProjectClient  # noqa: E402
from azure.identity import DefaultAzureCredential  # noqa: E402

from pdmops.common.config import StateStore  # noqa: E402
from pdmops.foundry._rest import project_endpoint  # noqa: E402


def main() -> None:
    questions = sys.argv[1:]
    if not questions:
        sys.exit(__doc__)
    state = StateStore()
    endpoint = project_endpoint(state.output("wave1", "foundryAccountName"),
                                state.output("wave1", "foundryProjectName"))
    agent_name = state.require("foundry_agent", "agentName")["agentName"]

    with DefaultAzureCredential() as credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
        client = project.get_openai_client(agent_name=agent_name)
        for q in questions:
            print("=" * 100)
            print("Q:", q)
            response = client.responses.create(input=q)
            print("status:", response.status, "| error:", getattr(response, "error", None))
            for item in response.output:
                kind = getattr(item, "type", "?")
                if kind == "function_call":
                    args = json.loads(item.arguments).get("kql", item.arguments)
                    print("\n  [KQL]", " ".join(str(args).split())[:400])
                elif kind == "function_call_output":
                    print("  [RESULT]", str(item.output)[:300].replace("\n", " "))
            print("\nA:", response.output_text)


if __name__ == "__main__":
    main()
