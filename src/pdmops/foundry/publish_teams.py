#!/usr/bin/env python
"""Publish the PdM Copilot to Microsoft Teams — the REST flow for a
network-isolated project (PNA disabled), per Microsoft's own guide:
learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network

Run this AFTER infra/wave3-bot.bicep has created the Bot Service (state.json
must have .wave3.botServiceId). This script does Steps 3 and 4 from that guide
— enabling the source-IP-filtered Activity Protocol route and calling the
microsoft365/publish API. Step 1 (agent identity) and Step 2 (Bot Service) are
already done by foundry/agent.py and Wave 3.

Two traps this script exists specifically to avoid:
  - The PATCH in Step 3 uses merge-patch semantics but REPLACES
    protocol_configuration and authorization_schemes wholesale — omitting
    "responses" here would silently break the agent's existing API access.
  - appVersion must be digits-and-periods only, can't start with 0, and can't
    be reused across publishes — this script auto-increments the patch
    component rather than hardcoding "1.0.0" on every run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore
from pdmops.common.logging_setup import setup_logging
from pdmops.foundry._rest import FoundryAgentRest

logger = setup_logging(__name__)


def _next_app_version(previous: str | None) -> str:
    if not previous:
        return "1.0.0"
    parts = previous.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["Shared", "Tenant"], default="Shared",
                         help="Shared = just you, no admin approval, fast loop (default). "
                              "Tenant = whole org, requires Microsoft 365 admin approval.")
    parser.add_argument("--developer-name", default="PdM", help="Max 32 chars.")
    parser.add_argument("--short-description", default="Triage at-risk assets and get maintenance recommendations.")
    parser.add_argument("--full-description",
                         default="Conversational predictive-maintenance copilot. Correlates real-time asset "
                                 "telemetry, predictions and downtime history, explains likely causes, and requests approval before taking "
                                 "any corrective action.")
    # The docs call these "optional metadata," but a live 400
    # ("DeveloperWebsiteUrl must be a valid HTTPS URL") confirmed the API
    # validates them regardless — omitting them isn't accepted as "no value."
    parser.add_argument("--developer-website-url", default="https://github.com/pranabpaul-tech/pdm-fabric-foundry")
    parser.add_argument("--privacy-url", default="https://github.com/pranabpaul-tech/pdm-fabric-foundry")
    parser.add_argument("--terms-url", default="https://github.com/pranabpaul-tech/pdm-fabric-foundry")
    parser.add_argument("--skip-network-toggle", action="store_true",
                         help="Don't lock the Foundry account back to private after a successful publish — "
                              "use if you want to keep testing with it public for a while first.")
    args = parser.parse_args()

    state = StateStore()
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")
    foundry_agent = state.require("foundry_agent", "agentName", "clientId")
    bot_service_id = state.output("wave3", "botServiceId")

    agent_name = foundry_agent["agentName"]
    rest = FoundryAgentRest(account_name, project_name)

    auth_scheme = "BotServiceRbac" if args.scope in ("Shared", "Personal") else "BotServiceTenant"

    logger.info("Step 3: enabling the public Activity Protocol route and %s auth scheme...", auth_scheme)
    rest.patch_agent(agent_name, {
        "agent_endpoint": {
            "protocol_configuration": {
                "responses": {},  # must be re-sent — PATCH replaces this block wholesale
                "activity": {
                    "enable_m365_public_endpoint": True,
                },
            },
            "authorization_schemes": [
                {"type": "Entra"},
                {"type": auth_scheme},
            ],
        }
    })

    previous_version = state.get("teams_publish", {}).get("appVersion")
    app_version = _next_app_version(previous_version)

    logger.info("Step 4: publishing to Microsoft 365 / Teams at scope=%s, appVersion=%s...", args.scope, app_version)
    result = rest.publish_to_microsoft365(agent_name, {
        "agentDisplayName": foundry_agent["agentName"],
        "botServiceArmId": bot_service_id,
        "publishScope": args.scope,
        "publishAsAutopilot": False,
        "appVersion": app_version,
        "shortDescription": args.short_description,
        "fullDescription": args.full_description,
        "developerName": args.developer_name,
        "developerWebsiteUrl": args.developer_website_url,
        "privacyUrl": args.privacy_url,
        "termsOfUseUrl": args.terms_url,
    })

    title_id = result.get("titleId")
    teams_app_id = result.get("teamsAppId")
    state.merge("teams_publish", {
        "appVersion": app_version,
        "scope": args.scope,
        "titleId": title_id,
        "teamsAppId": teams_app_id,
    })
    logger.info("Published. titleId=%s teamsAppId=%s", title_id, teams_app_id)
    if teams_app_id:
        # Confirmed against a live reference (pranabpaul-tech/foundry-iq-v2):
        # this is a real, working deep link — titleId alone isn't enough to
        # construct one.
        logger.info("Open directly in Teams: https://teams.microsoft.com/l/app/%s", teams_app_id)
    if args.scope == "Shared":
        logger.info("Or find it under 'Your agents' in the Teams/M365 agent store (can take ~1h for the "
                    "store cache to refresh — sign out/in to force it).")
    else:
        logger.info("Submitted for Microsoft 365 admin approval: "
                    "https://admin.cloud.microsoft/?#/agents/all/requested — it appears under "
                    "'Built by your org' once approved.")

    if not args.skip_network_toggle:
        from pdmops.common.foundry_network import set_foundry_public_access
        account_id = state.output("wave1", "foundryAccountId")
        logger.info("Locking the Foundry account back down to private now that publishing succeeded. "
                     "Takes a few minutes to actually take effect — a public call may briefly still "
                     "succeed right after this; that's expected propagation delay, not a bug. Real "
                     "Teams/Bot Service traffic keeps working via the enable_m365_public_endpoint "
                     "exception set above.")
        set_foundry_public_access(account_id, enabled=False)


if __name__ == "__main__":
    main()
