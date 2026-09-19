"""One helper: resolve a service principal's application (client) ID from its
object ID. Needed because KQL database viewer grants use the
`aadapp=<appId>;<tenantId>` principal format, while Bicep/ARM only ever hand
back a managed identity's object ID (`principalId`) — the two are different
GUIDs for the same identity.
"""
from __future__ import annotations

import requests

from .auth import get_token_provider
from .logging_setup import setup_logging

logger = setup_logging(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def resolve_service_principal_app_id(object_id: str) -> str:
    token = get_token_provider().get_token(GRAPH_SCOPE)
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{object_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"$select": "appId,displayName"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    logger.info("Resolved service principal %s -> appId %s", body.get("displayName"), body.get("appId"))
    return body["appId"]
