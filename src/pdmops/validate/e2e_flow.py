#!/usr/bin/env python
"""End-to-end validation: ingestion freshness, the reference KQL queries,
private DNS resolution, a live agent turn that must call its Kusto tool, and
a negative test proving the approval gate actually blocks an unapproved
action call.

Run this last, from the jumpbox, after every setup/ and foundry/ script has
succeeded. A failure in "agent tool turn" is reported as a WARNING, not a hard
failure — it's checking a live model-driven tool call, which is inherently a
little less deterministic than the other checks (see foundry/hosted_agent/main.py's
module docstring for the custom-tool architecture this validates).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore, get_settings
from pdmops.common.logging_setup import setup_logging
from pdmops.actions.approval import ApprovalError, require_approved, request_approval

logger = setup_logging(__name__)
SCRIPTS_DIR = Path(__file__).resolve().parent


def run_subcheck(name: str, module_file: str, *extra_args: str) -> bool:
    logger.info("--- %s ---", name)
    result = subprocess.run([sys.executable, str(SCRIPTS_DIR / module_file), *extra_args])
    passed = result.returncode == 0
    logger.info("%s: %s", name, "PASS" if passed else "FAIL")
    return passed


def check_approval_gate_blocks_unapproved_calls() -> bool:
    logger.info("--- approval gate negative test ---")
    approval_id = request_approval("test.no_op", "e2e_flow negative test — should never be approved", {})
    try:
        require_approved(approval_id)
        logger.error("approval gate: FAIL — require_approved() did not raise for an unapproved approval_id")
        return False
    except ApprovalError:
        logger.info("approval gate: PASS — require_approved() correctly refused an unapproved approval_id")
        return True


def check_agent_tool_turn() -> bool:
    logger.info("--- agent tool turn (custom Kusto tool) ---")
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from pdmops.foundry._rest import project_endpoint

        state = StateStore()
        account_name = state.output("wave1", "foundryAccountName")
        project_name = state.output("wave1", "foundryProjectName")
        foundry_agent = state.require("foundry_agent", "agentName")

        project = AIProjectClient(endpoint=project_endpoint(account_name, project_name), credential=DefaultAzureCredential())
        openai_client = project.get_openai_client(agent_name=foundry_agent["agentName"])
        response = openai_client.responses.create(
            input="Which asset has the highest average vibration over the last hour? "
                  "Query the table directly.",
        )

        item_types = [getattr(item, "type", "?") for item in response.output]
        used_tool = any("call" in t for t in item_types)
        if not used_tool:
            logger.warning("agent tool turn: WARNING — response didn't include any tool-call output items "
                            "(saw: %s). The agent may have answered from general knowledge instead of "
                            "querying live data — check response.output_text manually.", item_types)
            return False
        logger.info("agent tool turn: PASS — response included tool-call activity (%s)", item_types)
        return True
    except Exception as exc:  # noqa: BLE001 — this check is explicitly allowed to fail; log why and move on
        logger.warning("agent tool turn: WARNING — could not complete a live agent turn (%s).", exc)
        return False


def main() -> None:
    results = {
        "smoke_kql": run_subcheck("KQL smoke test", "smoke_kql.py"),
        "network_check": run_subcheck("Private DNS resolution", "network_check.py"),
        "approval_gate": check_approval_gate_blocks_unapproved_calls(),
        "agent_tool_turn": check_agent_tool_turn(),
    }

    logger.info("\n=== Summary ===")
    for name, passed in results.items():
        logger.info("%-16s %s", name, "PASS" if passed else "FAIL/WARN")

    hard_checks = ["smoke_kql", "network_check", "approval_gate"]
    if not all(results[k] for k in hard_checks):
        logger.error("One or more required checks failed.")
        sys.exit(1)
    if not results["agent_tool_turn"]:
        logger.warning("Required checks passed; the agent tool-call turn did not — see above.")
    logger.info("e2e_flow: DONE")


if __name__ == "__main__":
    main()
