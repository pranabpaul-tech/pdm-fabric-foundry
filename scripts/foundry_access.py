#!/usr/bin/env python
"""Show, open or close public network access on the Foundry account.

    python scripts/foundry_access.py status
    python scripts/foundry_access.py open      # needed before agent deploys, create_toolbox.py, ask_agent.py from outside the VNet
    python scripts/foundry_access.py close     # back to private; refuses if the agent's Teams route is missing

The account stays VNet-injected either way. After `open` it takes a few minutes for the data plane to answer; after
`close` a few minutes for the block to apply. Teams keeps working while closed through the Bot Service's source-IP-filtered
Activity Protocol route, PROVIDED that route is configured on the agent: `deploy_hosted_agent.py` restores it after every
deploy, and `close` here double-checks it while the data plane is still reachable.
"""
import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdmops.common.auth import AZURE_MGMT_SCOPE, get_token_provider  # noqa: E402
from pdmops.common.config import StateStore  # noqa: E402
from pdmops.common.foundry_network import ARM_API_VERSION, set_foundry_public_access  # noqa: E402
from pdmops.foundry._rest import FoundryAgentRest  # noqa: E402


def status(account_id: str) -> dict:
    token = get_token_provider().get_token(AZURE_MGMT_SCOPE)
    r = requests.get(f"https://management.azure.com{account_id}?api-version={ARM_API_VERSION}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    p = r.json()["properties"]
    return {"publicNetworkAccess": p.get("publicNetworkAccess"), "defaultAction": (p.get("networkAcls") or {}).get("defaultAction"),
            "vnetInjection": [n.get("scenario") for n in (p.get("networkInjections") or [])]}


def teams_route_ok(state: StateStore) -> bool | None:
    """True/False if the agent's endpoint config could be read; None if the data plane is not reachable."""
    try:
        agent = state.require("foundry_agent", "agentName")["agentName"]
        ep = FoundryAgentRest(state.output("wave1", "foundryAccountName"), state.output("wave1", "foundryProjectName")).get_agent(agent)
        return bool(((ep.get("agent_endpoint") or {}).get("protocol_configuration") or {}).get("activity", {}).get("enable_m365_public_endpoint"))
    except Exception:  # noqa: BLE001 - private: cannot check
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "open", "close"])
    parser.add_argument("--force", action="store_true", help="close even if the Teams route is missing")
    args = parser.parse_args()

    state = StateStore()
    account_id = state.output("wave1", "foundryAccountId")
    if args.action == "status":
        print(status(account_id))
        return
    if args.action == "close" and not args.force:
        ok = teams_route_ok(state)
        if ok is False:
            sys.exit("REFUSING to close: the agent has no Activity Protocol route, so Teams would stop reaching it. "
                     "Re-run publish_teams.py (or deploy_hosted_agent.py, which restores it), then close. --force overrides.")
        if ok is None:
            print("note: data plane not reachable from here, so the Teams route could not be double-checked")
    set_foundry_public_access(account_id, enabled=(args.action == "open"))
    print(status(account_id))
    print("Give it a few minutes to take effect." + (" Then deploy / test." if args.action == "open" else ""))


if __name__ == "__main__":
    main()
