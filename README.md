# PdM Fabric Foundry

Predictive maintenance on **Microsoft Fabric Real-Time Intelligence** with a **Foundry-hosted agent published to Microsoft Teams** (Azure Bot Service). Built on the deployment patterns proven in `foundry-iq-v2` and `fleet-ops-copilot`.

- Plan: [`DEPLOYMENT_PLAN_v2.md`](DEPLOYMENT_PLAN_v2.md) (architecture, KPIs, stages, gotchas). [`DEPLOYMENT_PLAN.md`](DEPLOYMENT_PLAN.md) is the superseded v1.
- This README: what exists in the repo today and how to deploy it.

## Status

| Area | State |
|---|---|
| Bicep waves 0-1 (network, F8 capacity, Key Vault, monitoring, ACR, jumpbox, vendored Foundry standard agent) | **Deployed** to `rg-pdm-fabric-foundry` (swedencentral): 45 resources, capability hosts `Succeeded`, account injected into `snet-agent`, public access still `Enabled` (required until the agent is published to Teams) |
| Bicep waves 2-3 (Fabric private link, Bot Service + Teams channel) | Adapted, compile; **not yet deployed** |
| Fabric setup 01-03 (workspace, Eventhouse, KQL schema) | **Run live**: workspace `pdm-fabric-foundry`, Eventhouse `pdmops-eventhouse`, all 9 tables / 5 functions / 2 materialized views applied |
| KQL data path | **Proven live** with the simulator: 720 raw -> 720 enriched rows through the update policy, views populated, `fn_anomalies` flags only the injected-fault asset (PUMP-03), no false positives |
| Downtime Eventstream (04) | Skipped: definition is still a placeholder (see below) |
| Hosted agent `pdm-orchestrator` (live telemetry lane: `asset_snapshot` + read-only `query_telemetry`) | **Deployed (version 6, `active`) and tested from the local machine** with `scripts/ask_agent.py`: correct triage of the faulty pump, healthy pump not over-alarmed, staleness reported. Not yet published to Teams |
| Teams publish (`foundry/publish_teams.py`, `infra/wave3-bot.bicep`) | Adapted from the reference; **not yet run** |
| Operations Agent | Item created from the PdM definition; portal *Generate Playbook* / message delivery / *Start* still manual |
| Downtime Eventstream definition | **Placeholder** - must be authored once in the portal and captured (`04_eventstream.py --capture`) |
| Lakehouse gold tables, notebooks/ML, semantic model, Activator, Foundry IQ KB, Fabric Data Agent toolbox, lakehouse T-SQL agent, approval-gated action tools | **Not started** (plan sections 7, 9, 11) |
| `actions/incident_store.py` | Local-file store from the reference - **does not work inside a hosted-agent container**; to be replaced by the Eventhouse-backed `approval_event` log before any write tool ships |

## Real-Time Manufacturing Jumpstart (installed)

The Jumpstart is installed in its own workspace `pdm-manufacturing-jumpstart` (17 items: 2 Eventstreams, Eventhouse `ManufacturingRealtimeAnalytics`,
Lakehouse `ManufacturingData`, 6 notebooks, pipeline, semantic model, report, dashboard, `TalkToManufacturingData` Data Agent) and the PdM layer is applied
on top of its Eventhouse. See [`docs/jumpstart-mapping.md`](docs/jumpstart-mapping.md) for the source -> PdM mapping, the data gaps and the install gotchas.

```powershell
# Python 3.10-3.13 venv (the library does not support 3.14)
py -3.13 -m venv .venv-js ; .venv-js\Scripts\python -m pip install fabric-jumpstart
$env:FABRIC_JUMPSTART_TOKEN_CREDENTIAL = "AzureCliCredential"
.venv-js\Scripts\python -c "import fabric_jumpstart as j; j.install('real-time-manufacturing', workspace_id='<new workspace id>', unattended=True)"
# then run PostDeploymentNotebook AND SimulateMachineData as separate jobs with _inlineInstallationEnabled=true (see the mapping doc)

python src/pdmops/setup/03_kql_schema.py --target jumpstart      # PdM layer on the Jumpstart Eventhouse
python src/pdmops/validate/smoke_jumpstart.py                    # verify
```

The earlier hand-built workspace (`pdm-fabric-foundry`, Eventhouse `pdmops-eventhouse`, simulator) is superseded for the data side but still what the deployed agent reads.

## Layout

```
azure.yaml                     azd project + pre/postprovision hooks
infra/                         Bicep waves: wave0-byo, main (wave 1), wave2-fabric-privatelink, wave3-bot
  modules/foundry-vendored/    Microsoft foundry-samples 15-private-network-standard-agent-setup (do not edit)
artifacts/kql/                 PdM Eventhouse schema (applied in order; optional/ is best-effort)
artifacts/ops-agent/           Fabric Operations Agent definition (captured format, PdM instructions)
artifacts/eventstream.downtime.definition.json   placeholder until captured
src/pdmops/setup/              01 workspace, 02 eventhouse, 03 kql schema, 04 eventstream, 05 network policy, 06 ops agent
src/pdmops/foundry/            hosted agent, deploy, Teams publish, REST helper
src/pdmops/actions/            approval gate + Power Automate action (approval-gated)
src/pdmops/validate/           smoke_kql, network_check, e2e_flow
scripts/                       capture_wave1_state.py, simulate_telemetry.py (dev only), test_agent_invoke.py
tests/                         offline tests (no Azure needed)
```

## Prerequisites

Same as the reference repos - see `DEPLOYMENT_PLAN_v2.md` section 5. In short:

- `az` and `azd`, signed in as a **real user** (not a service principal): `az login`, `azd auth login`.
- Python 3.11+.
- Resource providers registered: `Microsoft.Fabric`, `Microsoft.BotService`, `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.DocumentDB`, `Microsoft.Storage`, `Microsoft.KeyVault`, `Microsoft.ContainerRegistry`, `Microsoft.ContainerInstance`.
- Fabric admin portal tenant settings: **Azure Private Link** and **Configure workspace-level inbound network rules**, plus the Copilot / Azure OpenAI settings the Operations Agent and Data Agent need.
- **Fabric capacity quota** in the target region (default `swedencentral`; avoid `westus` and `eastus`):
  `az rest --method get --url "https://management.azure.com/subscriptions/<sub>/providers/Microsoft.Fabric/locations/<region>/usages?api-version=2023-11-01"`
- Model quota for `gpt-4.1` (and later `text-embedding-3-large`) in the same region.

## Offline checks (no Azure needed)

```powershell
python -m pip install pytest pydantic pydantic-settings python-dotenv azure-identity azure-kusto-data requests
python -m pytest tests -q
az bicep build --file infra/main.bicep --stdout > $null
```

## Deploy

### Wave 0 - BYO resources (Search, Storage, Cosmos)

```powershell
az group create -n rg-pdm-fabric-foundry -l swedencentral
az deployment group create -g rg-pdm-fabric-foundry -n wave0-byo-deployment -f infra/wave0-byo-resources.bicep
```

The preprovision hook reads this deployment's outputs; you do not copy IDs by hand.

### Wave 1 - platform + Fabric foundation

```powershell
azd env new pdm-dev
azd provision
```

`preprovision` prompts for the resource group, Foundry base name, Fabric capacity name, hosted-agent name and the capacity-admin UPN (Enter keeps defaults). `postprovision` then runs `setup/01`-`04` and `06`.
The Foundry capability host takes 30-35 minutes - expected. Then:

```powershell
python src/pdmops/validate/smoke_kql.py --schema-only     # tables, functions, views exist
```

### Prove the data path without the Jumpstart (dev only)

```powershell
python scripts/simulate_telemetry.py --i-know-this-is-dev
python src/pdmops/validate/smoke_kql.py                    # rows land, update policy fires, MVs + functions execute
# in the portal / KQL: fn_anomalies(3h) | where asset_id == 'PUMP-03'   (the simulator drifts this asset)
```

### Capture the downtime Eventstream (one manual step)

Author it once in the Fabric portal (Custom Endpoint -> Eventhouse table `downtime_raw`, JSON), then:

```powershell
python src/pdmops/setup/04_eventstream.py --capture <eventstreamId>   # commit the result
```

Later environments use `--apply`.

### Waves 2 and 3 (private link, hosted agent, Bot Service, Teams)

Follow `DEPLOYMENT_PLAN_v2.md` sections 8-10 and the ordering rules in `infra/README.md`. The important ones:

1. **Fabric items are built from the local machine, not the jumpbox.** Wave 2 (`deploy.ps1 -Wave 2`, then `05_network_policy.py --confirm`) is therefore the **last, optional** step: it locks the workspace to private access, after which local runs against Fabric stop working. Do it only once every Fabric item is built.
2. The Foundry account must be **public when the agent is deployed and published** (`publicNetworkAccessAtCreation = true`); `publish_teams.py` flips it back to private at the end.
3. `deploy_hosted_agent.py`, workspace-role grants, and `publish_teams.py` need a **delegated human** (`az login`, not a managed identity); on the jumpbox set `PDMOPS_FORCE_CLI_CREDENTIAL=1`.
4. `deploy.ps1 -Wave 3` deploys the Bot Service (`msaAppId` = the agent's `instance_identity.client_id`); then `publish_teams.py` prints the Teams deep link `https://teams.microsoft.com/l/app/<teamsAppId>`.
5. Test the agent directly (`scripts/test_agent_invoke.py`) **before** Bot Service / Teams. There is no Web Chat fallback for this design.

## Lessons from the first live deployment

- KQL reserved words bite: `latest`, `cycles` and `first` are keywords - bracket them or pick other names.
- An update policy that references another table (the `asset_dim` lookup) is **rejected while streaming ingestion is on** for the source table. `030_update_policies.kql` disables streaming on `telemetry_raw` and sets a 30 s batching window (about 30 s ingestion latency instead of sub-second).
- The PowerShell postprovision hook now stops on the first failing step; native command failures do not raise in `pwsh` by themselves.
- Bash on Windows rewrites arguments that start with `/subscriptions/...`; set `MSYS_NO_PATHCONV=1` when running `az` from Git Bash.
- `az ad signed-in-user show` can fail with a Conditional Access challenge even after `az login`; the object ID is also in the ARM token's `oid` claim, and the preprovision hook reads `OPERATOR_OBJECT_ID` / `FABRIC_ADMIN_UPN` from the azd env first.

## Lessons from testing the hosted agent (versions 1 -> 6)

Every one of these was found by asking the deployed agent real questions, not by reading code:

1. `ClientRequestProperties.set_option("servertimeout", ...)` needs a `timedelta`; a string made every query fail (v1).
2. Hour-aligned materialized-view bins make `ts > ago(1h)` return nothing; the agent then gave up after one empty result. Instructions now say which view to use for which window and to retry on zero rows (v3).
3. The model guessed column names for tables it had no schema for; give it exact columns (v4).
4. The model called a +0.8% change "sharply increasing", called stale data "<15 minutes old", and said an asset with 2x vibration was "within typical ranges" when it had no baseline. Instructions alone did not fix this.
5. **Fix that worked:** stop asking the model to do arithmetic. `hosted_agent/snapshot.py` computes baseline deltas, z-scores, materiality (>= 3 sigma AND >= 5 %), data age and staleness in code; the `asset_snapshot` tool returns them and the model only narrates (v6). The logic is unit-tested offline (`tests/test_snapshot.py`) with the exact failure cases.

Try it: `python scripts/ask_agent.py "Triage PUMP-03: is anything wrong?" "Is PUMP-01 healthy?"`

## Safety notes

- `query_telemetry` is read-only by construction: it calls `execute_query` (never `execute`), rejects leading `.` and external-data/plugin calls, caps rows, and its identity is granted database **viewer** only.
- Agent-proposed writes (SAP notification, dispositions) must go through `actions/approval.py`; nothing in the current agent can write.
- `scripts/simulate_telemetry.py` refuses to run without `--i-know-this-is-dev`.
- `state.json`, `.env`, `.azure/` are git-ignored; nothing in this repo stores secrets.
