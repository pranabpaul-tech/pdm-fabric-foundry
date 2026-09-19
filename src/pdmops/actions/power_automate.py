#!/usr/bin/env python
"""Action tool: trigger the Power Automate flow that creates a maintenance
notification (SAP PM) / notifies the duty technician. Gated on approval.require_approved —
refuses to call the flow at all without a matching approved token.

Returns a structured result the caller (the Foundry agent, via its tool-call
mechanism) must surface verbatim — the agent's instructions explicitly forbid
claiming success unless the tool call actually returned it.

The trigger URL is a secret: stored in Key Vault (kv-pdmops-*, secret name
below), read via managed identity / DefaultAzureCredential, never passed as a
literal or committed to state.json.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from pdmops.common.config import get_settings
from pdmops.common.logging_setup import setup_logging
from .approval import require_approved
from .incident_store import RecordStore

logger = setup_logging(__name__)

TRIGGER_URL_SECRET_NAME = "power-automate-trigger-url"


def _trigger_url() -> str:
    settings = get_settings()
    if not settings.key_vault_uri:
        raise RuntimeError("PDMOPS_KEY_VAULT_URI is not set — see .env.example.")
    client = SecretClient(vault_url=settings.key_vault_uri, credential=DefaultAzureCredential())
    return client.get_secret(TRIGGER_URL_SECRET_NAME).value


def create_maintenance_notification(approval_id: str, asset_id: str, failure_mode: str, urgency: str,
                                    summary: str, alert_id: str | None = None) -> dict[str, Any]:
    """The action tool the Foundry agent calls after an operator approves."""
    approval = require_approved(approval_id)  # raises ApprovalError if not approved — never proceeds silently

    payload = {
        "assetId": asset_id,
        "failureMode": failure_mode,
        "urgency": urgency,
        "alertId": alert_id,
        "summary": summary,
        "approvalId": approval_id,
        "approvedBy": approval.get("decidedBy"),
    }

    result: dict[str, Any]
    try:
        url = _trigger_url()
        resp = requests.post(url, json=payload, timeout=30)
        result = {
            "success": resp.status_code < 300,
            "statusCode": resp.status_code,
            "body": resp.text[:2000],
        }
    except requests.RequestException as exc:
        result = {"success": False, "statusCode": None, "body": str(exc)}

    RecordStore("actions").create({
        "actionType": "power_automate.create_maintenance_notification",
        "approvalId": approval_id,
        "payload": payload,
        "result": result,
    })

    if not result["success"]:
        logger.error("Power Automate trigger failed: %s", result)
    return result
