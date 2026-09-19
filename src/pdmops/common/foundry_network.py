"""Toggle the Foundry account's public network access.

Confirmed live (see README footnote [^3]): the hosted agent's own invocation
routes (`/endpoint/protocols/openai/`, and the `activityProtocol` route Teams
calls) only register correctly in Azure's internal routing layer if the
account is publicly reachable at the moment the agent is deployed and
published. An account left `Disabled` from creation hits a permanent —
not transient — `403 Traffic is not from an approved private endpoint` /
`404 Subdomain does not map to a resource` on every invocation, forever.

The account stays VNet-injected throughout this toggle (`networkInjections`
isn't affected), so its own connection to the Fabric Eventhouse stays on the
private link the whole time — only the account's inbound reachability
changes. Locking back down takes a few minutes to actually take effect
(confirmed live: a direct public call briefly still succeeded right after
re-disabling, then started correctly rejecting a few minutes later) — that's
an ordinary propagation delay, not a bug.
"""
from __future__ import annotations

import requests

from .auth import AZURE_MGMT_SCOPE, get_token_provider
from .logging_setup import setup_logging

logger = setup_logging(__name__)

ARM_API_VERSION = "2025-04-01-preview"


def set_foundry_public_access(account_id: str, *, enabled: bool) -> None:
    """account_id is the full ARM resource ID (state.json['wave1']['foundryAccountId'])."""
    token = get_token_provider().get_token(AZURE_MGMT_SCOPE)
    url = f"https://management.azure.com{account_id}?api-version={ARM_API_VERSION}"
    resp = requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "properties": {
                "publicNetworkAccess": "Enabled" if enabled else "Disabled",
                "networkAcls": {"defaultAction": "Allow" if enabled else "Deny"},
            }
        },
        timeout=60,
    )
    resp.raise_for_status()
    state = "public" if enabled else "private"
    logger.info("Foundry account %s set to %s (publicNetworkAccess/networkAcls).", account_id, state)
