# PdM Fabric Foundry

Predictive maintenance on **Microsoft Fabric Real-Time Intelligence** with a **Foundry-hosted agent published to Microsoft Teams** (Azure Bot Service). Built on the deployment patterns proven in `foundry-iq-v2` and `fleet-ops-copilot`.

- Data side: the **Real-Time Manufacturing Jumpstart** (installed) with a PdM layer on top - see [`docs/jumpstart-mapping.md`](docs/jumpstart-mapping.md).
- Agent side: a Foundry hosted agent (deployed, currently **not pointing at any live data**; see Status).
- The earlier written deployment plans were removed at the owner's request; this README and `docs/` are the current record.

## Status

| Area | State |
|---|---|
| Azure: waves 0-1 (network, F8 capacity, Key Vault, monitoring, ACR, jumpbox, vendored Foundry standard agent) | **Deployed** to `rg-pdm-fabric-foundry` (swedencentral): 45 resources, capability hosts `Succeeded`, Foundry account injected into `snet-agent`, public access still `Enabled`. Fabric capacity `pdmopsf8` (F8) was found paused once and had to be resumed |
| Fabric: Real-Time Manufacturing Jumpstart | **Installed** in workspace `pdm-manufacturing-jumpstart` (17 items). Simulator (`SimulateMachineData`) must be run as its own job; it loops up to 2 hours |
| Fabric: PdM layer on the Jumpstart Eventhouse | **Applied and verified** (`smoke_jumpstart.py`): 6 new tables, `pdm_asset_dim()`, `mv_machine_1m/1h`, `pdm_telemetry()`, `pdm_asset_latest()`, `pdm_silent_assets()`, `pdm_anomalies()`, `pdm_alerts_enabled()` |
| Hosted agent `pdm-orchestrator` (version 13) | **Deployed and tested** with `scripts/ask_agent.py`: name resolution, per-signal baseline judgement, OEE, honest "no downtime data" / unknown-machine answers, and the **Fabric Data Agent** through a user-token toolbox (first-pass yield by site: Munich 63.7% vs about 88% elsewhere). Tools: `asset_snapshot`, `query_telemetry` (read-only), `pdm-fabric-toolbox`, and four predictive-analysis tools (next row) |
| Predictive-analysis tools (`hosted_agent/analysis.py`) | `forecast_signal` (fitted line + 95% interval, significance test), `time_to_limit` (hours to an alarm limit; illustrative ISO 10816 vibration default, temperature/pressure need a user limit), `risk_ranking` (fleet triage with reasons), `oee_outlook` (good units lost per day if a quality/cycle gap persists, with a data-consistency check). **Trend and outlook only: no failure probability, no remaining useful life** (no failure history, no trained model); the agent is instructed to refuse those. On the live Jumpstart data every signal is flat, Munich's Gear Box ranks HIGH on quality alone. `scripts/analyze_local.py` runs the same code locally. 45 offline tests |
| Fabric Data Agent toolbox | `create_toolbox.py`: project connection `fabric-dataagent-obo` (`UserEntraToken`) + toolbox `pdm-fabric-toolbox` -> the Jumpstart's `TalkToManufacturingData` MCP endpoint. Runs as the asking user, so it works from Teams or an `az login` user but not for scheduled / app-only callers. Agent identity holds `Foundry User` on the project. Calls take about 25-30 s |
| Bot Service `bot-pdm-orchestrator` + Teams publish | **Deployed and published** (Shared scope, app version 1.0.1, auth `BotServiceRbac`): teamsAppId `a3cf3a77-ac30-4cb0-a190-d5da9b806cb9`, link `https://teams.microsoft.com/l/app/a3cf3a77-ac30-4cb0-a190-d5da9b806cb9`. **Foundry account is now private** (`publicNetworkAccess: Disabled`, still VNet-injected); Teams reaches the agent through the Bot Service's source-IP-filtered Activity Protocol route. **Not yet tried by a person in Teams** |
| Operations Agent `PdM Operations Monitor` | **Created in the Jumpstart workspace** with 3 rules over the Jumpstart data. Delivery of its alerts to Teams is **planned** (dashed in the diagram); Generate Playbook, message delivery and Start are manual portal steps |
| Azure wave 2 (Fabric private link) | Written, compiles; **not deployed**, and deliberately last: it would stop local builds reaching the workspace |
| Downtime data, Lakehouse gold tables, ML models, Activator, Foundry IQ KB, approval-gated write tools | **Not started** |
| `actions/incident_store.py` | Local-file store from the reference - does not work inside a hosted-agent container; to be replaced by the Eventhouse-backed `approval_event` log before any write tool ships |
| Hand-built workspace `pdm-fabric-foundry` (Eventhouse `pdmops-eventhouse`, Operations Agent) | **Deleted** on request. `artifacts/kql/`, `setup/01-04`, `06_ops_agent.py` and `simulate_telemetry.py` targeted it and are kept only as reference |

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

Same as the reference repos (`fleet-ops-copilot`, `foundry-iq-v2`). In short:

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

Follow the ordering rules in `infra/README.md`. The important ones:

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

## Teams

The agent is published to Microsoft Teams through Azure Bot Service (`infra/wave3-bot.bicep`, `src/pdmops/foundry/publish_teams.py`).
Open `https://teams.microsoft.com/l/app/<teamsAppId>` (id in `state.json['teams_publish']`) as a user who holds an Azure role on the Foundry project
(scope is `Shared`, auth scheme `BotServiceRbac`). It can also appear under "Your agents" after up to an hour. There is no Web Chat fallback for this design;
test the agent itself with `python scripts/ask_agent.py "..."`. `Tenant` scope (any tenant member, `BotServiceTenant`) needs Microsoft 365 admin approval.

The publish step needs a delegated token (your `az login`); Graph can answer with a Conditional Access challenge even after login, so
`auth.assert_delegated_identity()` falls back to reading the Fabric token's claims.

## Predictive analysis: what it can and cannot say

The Jumpstart data is 4 hours of stationary noise with one persistent quality outlier, and has no failure labels. So the agent forecasts trends (none exist), times limit crossings (none expected), ranks machines for triage and prices a quality gap in units. To get to failure probability / remaining useful life you need run-to-failure history: real downtime from SAP maintenance notifications, or synthetic / public run-to-failure data to build and validate the ML pipeline (labelled as such). Also note the simulated `production_quality` counts (about 2,280 items/h) contradict its cycle times (a 6.5 s cycle allows about 555/h); `oee_outlook` detects and warns about that.

## Foundry account is private: what that changes

- Direct calls (`ask_agent.py`, agent deploys, `create_toolbox.py`) from a machine outside the VNet now get `403` on the data plane. Run them from the jumpbox
  (`az container exec ...`), or temporarily re-enable public access (`foundry_network.set_foundry_public_access(account_id, enabled=True)`), do the work, and lock it again.
- **Deploy trap:** `update_details()` used to overwrite the agent endpoint with `responses` only, silently dropping the Activity Protocol route Teams needs.
  `deploy_hosted_agent.py` now captures the existing endpoint config first and restores it (regression test in `tests/test_agent_guardrails.py`).
- The Teams route cannot be probed from outside: its public exception is filtered to Bot Service / Microsoft 365 addresses. Verify with a real Teams message and the
  Bot Service `RequestsTraffic` metric.

## Safety notes

- `query_telemetry` is read-only by construction: it calls `execute_query` (never `execute`), rejects leading `.` and external-data/plugin calls, caps rows, and its identity is granted database **viewer** only.
- Agent-proposed writes (SAP notification, dispositions) must go through `actions/approval.py`; nothing in the current agent can write.
- `scripts/simulate_telemetry.py` refuses to run without `--i-know-this-is-dev`.
- `state.json`, `.env`, `.azure/` are git-ignored; nothing in this repo stores secrets.
