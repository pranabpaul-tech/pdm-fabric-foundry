#!/usr/bin/env python
"""Package foundry/hosted_agent/ as a container and deploy it as a Foundry
hosted agent (the MAF pattern) — a real Python app with a custom Kusto tool,
not a declarative Prompt Agent.

Default path: the SDK code-upload path (`create_version_from_code` with
`CodeDependencyResolution.REMOTE_BUILD`) — no image build step needed.

Fallback path (`--image`): register a pre-built container image instead.
Use this if REMOTE_BUILD fails with a `ProvisioningError` that doesn't
resolve on retry (seen live: identical failure and content_hash across
three consecutive attempts, despite both capability hosts and the SDK
version being fine — a platform-side issue with the source-upload path
specifically, not this project's config). To use it:

    az deployment group create --resource-group <rg> \\
      --template-file infra/modules/foundry.bicep ... enableContainerRegistry=true
    az acr update --name <acrName> --public-network-enabled true   # ACR Tasks'
      # build agent can't reach a private-only registry — temporary
    az acr build --registry <acrName> --image pdm-orchestrator:v1 \\
      --platform linux/amd64 src/pdmops/foundry/hosted_agent
    az acr update --name <acrName> --public-network-enabled false  # lock back down
    python deploy_hosted_agent.py --image <acrName>.azurecr.io/pdm-orchestrator:v1

Either path grants the deployed agent's own identity read access to the
Fabric workspace and its Kusto database — see `_grant_agent_data_access`
below — so its `query_telemetry` tool (in hosted_agent/main.py) can
query the Eventhouse directly, in-process, using its own credentials.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.auth import get_tenant_id
from pdmops.common.config import StateStore, get_settings
from pdmops.common.fabric_client import FabricClient
from pdmops.common.graph_client import resolve_service_principal_app_id
from pdmops.common.kusto_client import EventhouseKustoClient
from pdmops.common.logging_setup import setup_logging
from pdmops.foundry._rest import FoundryAgentRest, project_endpoint

logger = setup_logging(__name__)

# Set interactively by infra/hooks/preprovision.ps1|.sh into .env
# (PDMOPS_FOUNDRY_AGENT_NAME) — see that hook for why.
HOSTED_AGENT_NAME = get_settings().foundry_agent_name
SOURCE_DIR = Path(__file__).resolve().parent / "hosted_agent"


def _zip_source(source_dir: Path) -> Path:
    zip_path = Path(tempfile.gettempdir()) / f"{HOSTED_AGENT_NAME}.zip"
    excluded = {".git", ".venv", "__pycache__", ".env"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            zf.write(path, path.relative_to(source_dir))
    return zip_path


def _wait_for_active(project, created) -> None:
    for attempt in range(60):
        time.sleep(10)
        details = project.agents.get_version(agent_name=HOSTED_AGENT_NAME, agent_version=created.version)
        status = details["status"]
        logger.info("Provisioning status: %s (attempt %d/60)", status, attempt + 1)
        if status == "active":
            return
        if status == "failed":
            raise RuntimeError(f"Hosted agent provisioning failed: {dict(details)}")
    raise RuntimeError("Timed out waiting for the hosted agent version to become active.")


def _finish_deploy(state: StateStore, account_name: str, project_name: str, project, created,
                    query_uri: str, database_name: str) -> None:
    """Common tail shared by both the REMOTE_BUILD and pre-built-image paths:
    poll for active, route traffic to the new version, record state, grant
    data access."""
    from azure.ai.projects.models import (
        AgentEndpointConfig,
        FixedRatioVersionSelectionRule,
        ProtocolConfiguration,
        ResponsesProtocolConfiguration,
        VersionSelector,
    )

    _wait_for_active(project, created)

    project.agents.update_details(
        agent_name=HOSTED_AGENT_NAME,
        agent_endpoint=AgentEndpointConfig(
            version_selector=VersionSelector(
                version_selection_rules=[
                    FixedRatioVersionSelectionRule(agent_version=created.version, traffic_percentage=100),
                ]
            ),
            protocol_configuration=ProtocolConfiguration(responses=ResponsesProtocolConfiguration()),
        ),
    )

    # publish_teams.py needs instance_identity.client_id + the activityProtocol
    # endpoint — fetch both now rather than leaving it as a manual follow-up.
    rest = FoundryAgentRest(account_name, project_name)
    details = rest.get_agent(HOSTED_AGENT_NAME)
    identity = details.get("instance_identity", {})
    client_id = identity.get("client_id")
    if not client_id:
        raise RuntimeError(f"Hosted agent {HOSTED_AGENT_NAME} has no instance_identity.client_id yet. "
                            f"Full response: {details}")

    state.merge("foundry_agent", {
        "agentName": HOSTED_AGENT_NAME,
        "agentVersion": created.version,
        "kind": "hosted",
        "clientId": client_id,
        "principalId": identity.get("principal_id"),
        "activityEndpoint": rest.activity_protocol_endpoint(HOSTED_AGENT_NAME),
    })

    _grant_agent_data_access(state, identity.get("principal_id"), query_uri, database_name)
    logger.info("Done. state.json['foundry_agent'] updated. Next: infra/wave3-bot.bicep, then foundry/publish_teams.py.")


def deploy(model_name: str) -> None:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import CodeConfiguration, CodeDependencyResolution, HostedAgentDefinition, ProtocolVersionRecord
    from azure.identity import DefaultAzureCredential

    state = StateStore()
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")
    state.require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    query_uri = state.output("eventhouse", "queryServiceUri")
    database_name = state.output("eventhouse", "kqlDatabaseName")

    endpoint = project_endpoint(account_name, project_name)
    zip_path = _zip_source(SOURCE_DIR)
    logger.info("Packaged %s -> %s", SOURCE_DIR, zip_path)

    with zip_path.open("rb") as code_stream, DefaultAzureCredential() as credential, \
            AIProjectClient(endpoint=endpoint, credential=credential) as project:

        created = project.agents.create_version_from_code(
            agent_name=HOSTED_AGENT_NAME,
            description="PdM Copilot - triage and investigation of at-risk assets.",
            definition=HostedAgentDefinition(
                cpu="1",
                memory="2Gi",
                code_configuration=CodeConfiguration(
                    runtime="python_3_13",
                    entry_point=["python", "main.py"],
                    dependency_resolution=CodeDependencyResolution.REMOTE_BUILD,
                ),
                environment_variables={
                    # FOUNDRY_PROJECT_ENDPOINT is deliberately NOT set here — a
                    # live reference found FOUNDRY_*/AGENT_* env var names are
                    # platform-reserved and rejected; it's auto-injected.
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_name,
                    "EVENTHOUSE_QUERY_URI": query_uri,
                    "EVENTHOUSE_DATABASE_NAME": database_name,
                },
                protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
            ),
            code=code_stream,
        )
        logger.info("Created hosted agent version %s", created.version)
        _finish_deploy(state, account_name, project_name, project, created, query_uri, database_name)


def deploy_from_image(image: str, model_name: str) -> None:
    """Fallback path — register a pre-built container image instead of
    letting the platform build one from source. See this module's docstring
    for the full build-and-push sequence."""
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord
    from azure.identity import DefaultAzureCredential

    state = StateStore()
    account_name = state.output("wave1", "foundryAccountName")
    project_name = state.output("wave1", "foundryProjectName")
    state.require("eventhouse", "queryServiceUri", "kqlDatabaseName")
    query_uri = state.output("eventhouse", "queryServiceUri")
    database_name = state.output("eventhouse", "kqlDatabaseName")

    endpoint = project_endpoint(account_name, project_name)

    with DefaultAzureCredential() as credential, AIProjectClient(endpoint=endpoint, credential=credential) as project:
        created = project.agents.create_version(
            agent_name=HOSTED_AGENT_NAME,
            description="PdM Copilot - triage and investigation of at-risk assets.",
            definition=HostedAgentDefinition(
                cpu="1",
                memory="2Gi",
                container_configuration=ContainerConfiguration(image=image),
                environment_variables={
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_name,
                    "EVENTHOUSE_QUERY_URI": query_uri,
                    "EVENTHOUSE_DATABASE_NAME": database_name,
                },
                protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
            ),
        )
        logger.info("Created hosted agent version %s from image %s", created.version, image)
        _finish_deploy(state, account_name, project_name, project, created, query_uri, database_name)


def _grant_agent_data_access(state: StateStore, agent_principal_id: str, query_uri: str, database_name: str) -> None:
    """The hosted agent's custom query_telemetry tool needs its own
    identity to have Fabric workspace Viewer (so the workspace's Kusto engine
    accepts the connection at all) and Kusto database Viewer (so it can
    actually read table data) — both idempotent, safe to re-run.

    The workspace-role grant needs Fabric Admin/Member on the workspace —
    confirmed live that the ACI jumpbox's own identity doesn't have this
    (it only has Foundry Project Manager, granted for the Foundry calls
    this script also makes) and fails with 403 InsufficientPrivileges. If
    this script is run from the jumpbox, that grant needs to be re-run from
    a delegated human session instead (e.g. your own machine, `az login`)."""
    workspace_id = state.output("workspace", "workspaceId")

    fabric = FabricClient()
    try:
        fabric.grant_workspace_role(workspace_id, agent_principal_id, "ServicePrincipal", "Viewer")
        logger.info("Granted hosted agent Viewer on Fabric workspace %s.", workspace_id)
    except Exception as exc:  # noqa: BLE001 — surfaced as guidance, not swallowed
        logger.warning(
            "Could not grant workspace Viewer to the hosted agent (%s). If this ran from the jumpbox, "
            "its own identity likely lacks Fabric Admin/Member on the workspace — re-run just this grant "
            "from a machine signed in as a delegated human "
            "(FabricClient().grant_workspace_role('%s', '%s', 'ServicePrincipal', 'Viewer')).",
            exc, workspace_id, agent_principal_id,
        )

    app_id = resolve_service_principal_app_id(agent_principal_id)
    tenant_id = get_tenant_id()
    kusto = EventhouseKustoClient(query_uri, database_name)
    try:
        kusto.grant_database_viewer(app_id, tenant_id)
        logger.info("Granted hosted agent (appId %s) Viewer on database %s.", app_id, database_name)
    finally:
        kusto.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--image", metavar="REGISTRY/REPO:TAG",
                         help="Register this pre-built image instead of building from source — the "
                              "documented fallback when REMOTE_BUILD hits a platform-side ProvisioningError.")
    parser.add_argument("--skip-network-toggle", action="store_true",
                         help="Don't flip the Foundry account public before deploying — use only if you've "
                              "already made it public yourself (e.g. re-running this after a failed attempt).")
    args = parser.parse_args()

    if not args.skip_network_toggle:
        from pdmops.common.foundry_network import set_foundry_public_access
        state = StateStore()
        account_id = state.output("wave1", "foundryAccountId")
        logger.info("Temporarily making the Foundry account public — required for the hosted agent's "
                     "invocation routes to register correctly (see README footnote [^3]). It stays public "
                     "through Bot Service deploy and Teams publish; lock it back down after those succeed.")
        set_foundry_public_access(account_id, enabled=True)

    if args.image:
        deploy_from_image(args.image, args.model)
    else:
        deploy(args.model)


if __name__ == "__main__":
    main()
