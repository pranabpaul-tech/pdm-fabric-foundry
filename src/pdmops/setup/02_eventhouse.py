#!/usr/bin/env python
"""Create the Eventhouse and capture its default KQL database's connection URIs.

Fabric auto-provisions a default KQL database with the same display name as the
Eventhouse when the Eventhouse is created — this script waits for that database
to show up in the workspace's kqlDatabases list rather than trying to create one
itself.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import get_tenant_id
from pdmops.common.config import StateStore, get_settings
from pdmops.common.fabric_client import FabricClient
from pdmops.common.graph_client import resolve_service_principal_app_id
from pdmops.common.kusto_client import EventhouseKustoClient
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)


def _find_default_database(client: FabricClient, workspace_id: str, eventhouse_display_name: str, timeout_s: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        databases = client.request("GET", f"/workspaces/{workspace_id}/kqlDatabases").json().get("value", [])
        match = next((d for d in databases if d.get("displayName") == eventhouse_display_name), None)
        if match:
            return match
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"No KQL database named '{eventhouse_display_name}' appeared in workspace {workspace_id} "
                f"within {timeout_s}s. The default database is usually auto-created within a minute or two "
                f"of the Eventhouse itself — if this keeps failing, check the portal instead of re-running blind."
            )
        time.sleep(5)


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()

    settings = get_settings()
    state = StateStore()
    workspace_id = state.output("workspace", "workspaceId")

    client = FabricClient()
    existing = state.get("eventhouse")
    if existing and existing.get("eventhouseId"):
        logger.info("Eventhouse already recorded in state.json — nothing to do.")
        return

    logger.info("Creating Eventhouse '%s'...", settings.eventhouse_name)
    eventhouse = client.call("POST", f"/workspaces/{workspace_id}/eventhouses", {
        "displayName": settings.eventhouse_name,
    })
    eventhouse_id = eventhouse["id"]
    logger.info("Eventhouse created: %s", eventhouse_id)

    logger.info("Waiting for the default KQL database to appear...")
    database = _find_default_database(client, workspace_id, settings.eventhouse_name)
    database_id = database["id"]

    # queryServiceUri / ingestionServiceUri live under the database's own
    # properties, not the eventhouse's — fetch it directly to be sure we have
    # the fully-provisioned properties rather than the list view's summary.
    database_full = client.request("GET", f"/workspaces/{workspace_id}/kqlDatabases/{database_id}").json()
    props = database_full.get("properties", {})
    query_uri = props.get("queryServiceUri")
    ingest_uri = props.get("ingestionServiceUri")
    if not query_uri or not ingest_uri:
        raise RuntimeError(
            f"KQL database {database_id} is missing queryServiceUri/ingestionServiceUri in its properties — "
            f"it may still be provisioning. Re-run this script in a minute. Got: {props}"
        )

    state.merge("eventhouse", {
        "eventhouseId": eventhouse_id,
        "eventhouseName": settings.eventhouse_name,
        "kqlDatabaseId": database_id,
        "kqlDatabaseName": database["displayName"],
        "queryServiceUri": query_uri,
        "ingestionServiceUri": ingest_uri,
    })
    logger.info("Done. state.json['eventhouse'] updated. queryServiceUri=%s", query_uri)

    _grant_jumpbox_access(state, query_uri, database["displayName"])


def _grant_jumpbox_access(state: StateStore, query_uri: str, database_name: str) -> None:
    """The jumpbox's own identity needs to query the Eventhouse directly for
    validate/smoke_kql.py and manual troubleshooting — grant it once, here,
    rather than as a one-off manual step."""
    jumpbox_principal_id = state.get("wave1", {}).get("jumpboxPrincipalId", {})
    jumpbox_principal_id = jumpbox_principal_id.get("value") if isinstance(jumpbox_principal_id, dict) else jumpbox_principal_id
    if not jumpbox_principal_id:
        logger.warning("No wave1.jumpboxPrincipalId in state.json — skipping the jumpbox's Kusto access grant. "
                        "Grant it manually if validate/smoke_kql.py fails with 403 from the jumpbox.")
        return

    app_id = resolve_service_principal_app_id(jumpbox_principal_id)
    tenant_id = get_tenant_id()
    client = EventhouseKustoClient(query_uri, database_name)
    try:
        client.grant_database_viewer(app_id, tenant_id)
        logger.info("Granted jumpbox (appId %s) Viewer on database %s.", app_id, database_name)
    finally:
        client.close()


if __name__ == "__main__":
    main()
