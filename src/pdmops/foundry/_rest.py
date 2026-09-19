"""Small REST helper for the Foundry Agents v1 API calls that the SDK doesn't
(yet) cover cleanly — getting an agent's instance_identity, and the
protocol_configuration / microsoft365 publish calls, which are documented as
raw REST in Microsoft's own publish-to-Teams guide
(learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network).

Run these from a client that can reach the project's private endpoint — the
jumpbox, not your laptop — once the workspace network lockdown is in place.
"""
from __future__ import annotations

import requests

from ..common.auth import get_token_provider
from ..common.logging_setup import setup_logging

logger = setup_logging(__name__)

AI_AZURE_SCOPE = "https://ai.azure.com/.default"
API_VERSION = "v1"


def project_endpoint(account_name: str, project_name: str) -> str:
    return f"https://{account_name}.services.ai.azure.com/api/projects/{project_name}"


class FoundryAgentRest:
    def __init__(self, account_name: str, project_name: str):
        self.base = project_endpoint(account_name, project_name)
        self._tokens = get_token_provider()

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tokens.get_token(AI_AZURE_SCOPE)}",
            "Content-Type": content_type,
        }

    def get_agent(self, agent_name: str) -> dict:
        resp = requests.get(
            f"{self.base}/agents/{agent_name}",
            params={"api-version": API_VERSION},
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def patch_agent(self, agent_name: str, merge_patch_body: dict) -> dict:
        """Uses application/merge-patch+json — per the docs this REPLACES
        protocol_configuration and authorization_schemes wholesale, so callers
        must include every scheme/protocol they want to keep."""
        resp = requests.patch(
            f"{self.base}/agents/{agent_name}",
            params={"api-version": API_VERSION},
            headers=self._headers("application/merge-patch+json"),
            json=merge_patch_body,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"PATCH agent {agent_name} failed ({resp.status_code}): {resp.text[:2000]}")
        return resp.json() if resp.content else {}

    def publish_to_microsoft365(self, agent_name: str, body: dict) -> dict:
        resp = requests.post(
            f"{self.base}/agents/{agent_name}/microsoft365/publish",
            params={"api-version": API_VERSION},
            headers=self._headers(),
            json=body,
            timeout=60,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"microsoft365/publish failed ({resp.status_code}): {resp.text[:2000]}")
        return resp.json()

    def activity_protocol_endpoint(self, agent_name: str) -> str:
        return (
            f"{self.base}/agents/{agent_name}/endpoint/protocols/activityProtocol"
            f"?api-version=2025-05-15-preview"
        )
