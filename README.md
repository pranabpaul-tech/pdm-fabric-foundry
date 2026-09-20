# PdM Fabric Foundry

A predictive-maintenance copilot on **Microsoft Fabric Real-Time Intelligence** and **Microsoft Foundry**, published to **Microsoft Teams** through Azure Bot Service.

The data side is the **Real-Time Manufacturing Jumpstart** (machine telemetry, SAP master data, quality data) with a PdM layer added to its Eventhouse. On top sit a Foundry-hosted agent that judges machine health, forecasts trends and ranks the fleet, a path to the Jumpstart's **Fabric Data Agent** for historical questions, and a Fabric **Operations Agent** for monitoring. Deployment patterns come from two earlier projects, `fleet-ops-copilot` and `foundry-iq-v2`.

![Architecture: local scripts deploy a Jumpstart-based Fabric workspace and a private Foundry agent that Teams reaches through a Bot Service](docs/architecture.svg)

*Solid boxes are deployed and were exercised. Dashed boxes exist but were not exercised (empty tables, an agent not yet started, an idle jumpbox). Dotted boxes are not built. The dashed arrow (Operations Agent alerts to Teams) is planned. A PNG of the same diagram is in [`docs/architecture.png`](docs/architecture.png).*

## Where things stand

| Area | State |
|---|---|
| **Azure platform** | Deployed to `rg-pdm-fabric-foundry` (swedencentral): 45 resources. VNet, F8 Fabric capacity `pdmopsf8`, Key Vault, monitoring, ACR, jumpbox, and a Foundry account and project (`gpt-4.1`) with capability hosts, injected into `snet-agent`. |
| **Foundry account network** | **Private** (`publicNetworkAccess: Disabled`). Reopen it for deploys with `scripts/foundry_access.py open`, then close it again. |
| **Fabric data** | Jumpstart installed in workspace `pdm-manufacturing-jumpstart` (17 items). The PdM layer is applied to its Eventhouse and passes `validate/smoke_jumpstart.py`. See [`docs/jumpstart-mapping.md`](docs/jumpstart-mapping.md). |
| **Hosted agent** `pdm-orchestrator` | Version 13, tested with `scripts/ask_agent.py`. Six read-only tools plus the Fabric Data Agent toolbox (see below). |
| **Fabric Data Agent path** | Toolbox `pdm-fabric-toolbox` to the Jumpstart's `TalkToManufacturingData`, using the asking user's own token. Works for a person (Teams or an `az login` user), not for scheduled or app-only callers. Calls take about 25 to 30 seconds. |
| **Teams** | Bot Service `bot-pdm-orchestrator` deployed; app published at `Shared` scope (version 1.0.1). **Not yet tried by a person in Teams**, so access to the private agent through the Bot Service is configured but not demonstrated. |
| **Operations Agent** | `PdM Operations Monitor` created in the Jumpstart workspace with three rules. Delivery of its alerts to Teams is planned; *Generate Playbook*, message delivery and *Start* are manual portal steps and not done. |
| **Not built** | Downtime feed, failure and remaining-useful-life models, Activator and SAP loop, Foundry IQ knowledge base, approval-gated write tools, Fabric private link (Wave 2, deliberately last). |

The Jumpstart's simulator (`SimulateMachineData`) loops for up to two hours per run and must be started as its own job. The F8 capacity bills while it is active.

## The agent

`pdm-orchestrator` is a Microsoft Agent Framework hosted agent. **The model narrates; the numbers are computed in code**, in modules that are unit-tested offline. That split came from testing (see Lessons learned): given raw data, the model called a +0.8% change "sharply increasing", called stale data fresh, and called a doubled vibration reading "normal".

| Tool | What it does | Code |
|---|---|---|
| `asset_snapshot(asset_id)` | Per-signal comparison with the machine's own baseline (mean, standard deviation, z-score, % change), labelled NORMAL / ABNORMAL / INSUFFICIENT_BASELINE (abnormal needs at least 3 standard deviations and 5%); data age and staleness; identity, OEE parts, predictions, alerts, downtime. | `hosted_agent/snapshot.py` |
| `query_telemetry(kql)` | Free-form **read-only** KQL with a row cap. Uses `execute_query` (never `execute`), rejects a leading `.` and external-data or plugin calls, and the identity holds database *viewer* only. | `hosted_agent/main.py` |
| `forecast_signal` | Fitted line with slope, 95% interval, significance test and a forecast; flat when there is no significant trend. | `hosted_agent/analysis.py` |
| `time_to_limit` | Hours until an alarm limit would be crossed at the fitted trend. Vibration defaults to an illustrative ISO 10816 limit; temperature and pressure require a limit from the user. | `analysis.py` |
| `risk_ranking` | Fleet triage order (HIGH / MEDIUM / UNKNOWN / LOW) with reasons: own-baseline shifts, quality gaps against the fleet median, significant trends. | `analysis.py` |
| `oee_outlook(asset_id)` | Good units lost per day if a quality or cycle-time gap persists, with assumptions and a check that recorded throughput matches cycle time. | `analysis.py` |
| Fabric Data Agent (toolbox) | Natural-language questions over the Lakehouse and Eventhouse: historical OEE, yield by site, cross-site comparisons. | `foundry/create_toolbox.py` |

### What the predictive tools can and cannot say

They are **trend and outlook** tools. There is no failure history and no trained model, so the agent never gives a probability of failure, a remaining useful life, or a date a machine "will fail", and its instructions require it to say so. On the current data every signal is flat noise, so forecasts are flat and no limit crossing is expected. The one real signal is a persistent quality outlier: the Munich Gear Box (machine 103) has about 63% first-pass yield against about 88% elsewhere.

To get to real failure prediction you need run-to-failure history: real downtime from SAP maintenance notifications and work orders, or synthetic and public run-to-failure data to build and validate the ML pipeline (labelled as such). Note also that the simulated production counts (about 2,280 items per hour) contradict the cycle times (a 6.5 s cycle allows about 555 per hour); `oee_outlook` detects and warns about that.

Try it (the Foundry account must be reachable, see "Working with the private Foundry account"):

```powershell
python scripts/ask_agent.py "Which machine should I look at first?" "Is the Gear Box healthy?" "Will the Gear Box fail in the next 24 hours?"
python scripts/analyze_local.py 103 vibration_mms     # the same analysis code, run locally against the Eventhouse
```

## Data

The Jumpstart provides two Eventstreams, the Eventhouse `ManufacturingRealtimeAnalytics` (`machineraw` and `mqttdataraw` flattened into `sensors_parsed` and `production_quality`, plus SAP `machines_internal` and `sites_internal`), a Lakehouse, notebooks, a pipeline, a semantic model, a report, a dashboard and the Data Agent. Its Activator and KQL Queryset did not deploy.

This repo adds, in `artifacts/kql-jumpstart/` (nothing of the Jumpstart is changed): six empty tables (`downtime_raw`, `prediction_stream`, `alert_event`, `alert_disposition`, `approval_event`, `pdm_config`), `pdm_asset_dim()`, the wide per-minute views `mv_machine_1m` and `mv_machine_1h`, and `pdm_telemetry()`, `pdm_asset_latest()`, `pdm_silent_assets()`, `pdm_anomalies()`, `pdm_alerts_enabled()`.

Gaps in the source data that limit what can honestly be claimed: no downtime events (so no MTBF or MTTR), no current sensor, no criticality or manufacturer, and sensor values that are random noise within fixed ranges. Details in [`docs/jumpstart-mapping.md`](docs/jumpstart-mapping.md).

## Repository layout

```
azure.yaml, infra/                Bicep waves 0-3 and azd hooks (Wave 2, Fabric private link, is written but not deployed)
  modules/foundry-vendored/       Microsoft foundry-samples private standard agent setup (do not edit)
artifacts/kql-jumpstart/          ACTIVE: the PdM layer applied to the Jumpstart Eventhouse
artifacts/ops-agent/              ACTIVE: Operations Agent definition (three rules over the Jumpstart data)
artifacts/kql/                    reference only: schema for an earlier hand-built Eventhouse (deleted workspace)
artifacts/eventstream.downtime.definition.json   placeholder, to be captured from the portal
docs/                             architecture diagram (SVG + PNG) and the Jumpstart mapping
src/pdmops/
  common/                         auth, Fabric REST client, Kusto client, config and the state.json store
  setup/                          03_kql_schema.py (--target jumpstart) and 06_ops_agent.py are active;
                                  01, 02, 04 and 05 targeted the deleted workspace and are reference
  foundry/                        deploy_hosted_agent.py, create_toolbox.py, publish_teams.py, _rest.py
    hosted_agent/                 main.py (agent), snapshot.py and analysis.py (deterministic logic), Dockerfile
  validate/                       smoke_jumpstart.py is active; smoke_kql.py and e2e_flow.py are reference
  actions/                        approval gate and Power Automate action (nothing in the agent can write yet)
scripts/                          ask_agent.py, analyze_local.py, foundry_access.py, js_status.py,
                                  capture_wave1_state.py, simulate_telemetry.py (old dev-only simulator)
tests/                            45 offline tests, no Azure needed
```

`state.json` (git-ignored) is the seam between Bicep outputs and Python-created IDs. Its `workspace` and `eventhouse` sections are aliases of `jumpstart_workspace` and `jumpstart_eventhouse`, so scripts written for the earlier layout follow the Jumpstart. Other sections: `wave1`, `wave3`, `foundry_agent`, `data_agent`, `toolbox`, `teams_publish`, `ops_agent`.

## Prerequisites

- `az` and `azd`, signed in as a **real user** (`az login`, `azd auth login`). Python 3.11+ for this repo; **3.10 to 3.13** for `fabric-jumpstart`.
- Resource providers: `Microsoft.Fabric`, `Microsoft.BotService`, `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.DocumentDB`, `Microsoft.Storage`, `Microsoft.KeyVault`, `Microsoft.ContainerRegistry`, `Microsoft.ContainerInstance`.
- Fabric tenant settings for the Operations Agent and Data Agent (Copilot and Azure OpenAI switches). Azure Private Link and workspace-level inbound rules only if you deploy Wave 2.
- Fabric capacity quota and `gpt-4.1` model quota in the region. Use `swedencentral`; avoid `westus` (hosted-agent routes took 15 to 40+ minutes to register) and `eastus` (no Operations Agent).

## Run the offline checks

```powershell
python -m pip install pytest pydantic pydantic-settings python-dotenv azure-identity azure-kusto-data requests
python -m pytest tests -q          # 45 tests
az bicep build --file infra/main.bicep --stdout > $null
```

## Deploy, in order

**Fabric items are built from the local machine, not the jumpbox**, so Wave 2 (which locks the Fabric workspace to private access) is last and optional. On Windows with Git Bash, set `MSYS_NO_PATHCONV=1` for `az` arguments that start with `/subscriptions/`.

1. **Azure (Waves 0 and 1).**
   ```powershell
   az group create -n rg-pdm-fabric-foundry -l swedencentral
   az deployment group create -g rg-pdm-fabric-foundry -n wave0-byo-deployment -f infra/wave0-byo-resources.bicep
   azd env new pdm-dev ; azd provision      # about 20 minutes; set FABRIC_ADMIN_UPN and OPERATOR_OBJECT_ID in the azd env if Graph is challenged
   ```
2. **Jumpstart** into a new workspace on the same capacity.
   ```powershell
   py -3.13 -m venv .venv-js ; .venv-js\Scripts\python -m pip install fabric-jumpstart
   $env:FABRIC_JUMPSTART_TOKEN_CREDENTIAL = "AzureCliCredential"
   .venv-js\Scripts\python -c "import fabric_jumpstart as j; j.install('real-time-manufacturing', workspace_id='<new workspace id>', unattended=True)"
   ```
   `PostDeploymentNotebook` needs the run parameter `_inlineInstallationEnabled=true`, and its last cell (`%run SimulateMachineData`) fails inside the run: run **`SimulateMachineData` as its own job** with the same parameter. Track it with `scripts/js_status.py`.
3. **PdM layer and Operations Agent.**
   ```powershell
   python src/pdmops/setup/03_kql_schema.py --target jumpstart
   python src/pdmops/validate/smoke_jumpstart.py
   python src/pdmops/setup/06_ops_agent.py --apply        # then Generate Playbook, message delivery and Start in the portal
   ```
4. **Foundry agent** (the account must be reachable: `python scripts/foundry_access.py open`, then wait a few minutes).
   ```powershell
   python src/pdmops/foundry/create_toolbox.py            # connection fabric-dataagent-obo + toolbox pdm-fabric-toolbox
   # grant the agent identity "Foundry User" on the project:
   #   az role assignment create --assignee-object-id <agent principalId> --assignee-principal-type ServicePrincipal --role "Foundry User" --scope <project id>
   python src/pdmops/foundry/deploy_hosted_agent.py       # builds from source, routes traffic, keeps the Teams route
   python scripts/ask_agent.py "Is the Gear Box healthy?"
   ```
   `deploy_hosted_agent.py` also grants the agent's identity workspace *Viewer* and Eventhouse *viewer*. If its Graph lookup for the agent's app ID is challenged, make the Eventhouse grant directly with `EventhouseKustoClient.grant_database_viewer`.
5. **Bot Service and Teams.**
   ```powershell
   az deployment group create -g rg-pdm-fabric-foundry -n wave3-bot -f infra/wave3-bot.bicep --parameters botName=bot-pdm-orchestrator "botDisplayName=PdM Copilot" msaAppId=<agent clientId from state.json> "activityEndpoint=<activityEndpoint from state.json>"
   python src/pdmops/foundry/publish_teams.py --scope Shared --skip-network-toggle
   python scripts/foundry_access.py close                 # back to private
   ```
   Open `https://teams.microsoft.com/l/app/<teamsAppId>` (id in `state.json['teams_publish']`) as a user holding an Azure role on the Foundry project. `Shared` scope with `BotServiceRbac` needs no admin approval; `Tenant` scope (any tenant member, `BotServiceTenant`) needs Microsoft 365 admin approval. There is no Web Chat fallback for this design, and the app may take up to an hour to appear under "Your agents" (the direct link works).

## Working with the private Foundry account

- From outside the VNet, direct calls (`ask_agent.py`, deploys, `create_toolbox.py`) get `403 Public access is disabled`. Use `scripts/foundry_access.py open`, do the work, then `close`. `close` refuses if it can see that the agent's Teams route is missing.
- **Deploy trap:** `update_details()` rewrites the agent endpoint with the `responses` protocol only, which silently **drops the Activity Protocol route Teams needs**; the agent still answers the Responses API, so nothing looks broken. `deploy_hosted_agent.py` now captures the endpoint config first and restores it (regression test in `tests/test_agent_guardrails.py`).
- The Teams route cannot be probed from outside: its public exception is filtered to Bot Service and Microsoft 365 addresses, and an unauthenticated probe returns `401` whether or not the network rule is in place. The only proof is a real Teams message plus the Bot Service `RequestsTraffic` metric (`az monitor metrics list --resource <bot id> --metric RequestsTraffic --filter "StatusCode eq '*'"`).

## Lessons learned

Each was found by running the real thing, not by reading code.

- **KQL:** `latest`, `cycles` and `first` are reserved words. An update policy that references another table is rejected while streaming ingestion is on for the source table. `create-or-alter materialized-view ... with (backfill = true)` is only valid the first time; the schema applier drops the option for existing views. A detector that needs history (`pdm_anomalies`) flags every asset if it runs on a padded window before real data exists.
- **Kusto SDK:** `servertimeout` needs a `timedelta`, not a string.
- **Agent quality:** models guess column names, mis-state the current time, and misjudge small and large changes, and instructions alone did not fix that. Moving baseline maths, materiality, data age and forecasts into tested code did. Also: never let an instruction assert the state of data ("the table is empty"), because it goes stale; have the agent check. And forbid promising results "later": an agent answers once.
- **Fabric Data Agent:** works through a user-token toolbox, needs the agent identity to hold `Foundry User` on the project, and sometimes answers that it has no data access before working on retry.
- **Jumpstart:** `PostDeploymentNotebook` fails through the job API without `_inlineInstallationEnabled`, and its `%run SimulateMachineData` step fails regardless. The capacity was found paused once, suspended by another principal.
- **Identity and tooling:** Graph can answer with a Conditional Access challenge even after `az login`; the object ID is in the ARM token's `oid` claim, and `assert_delegated_identity()` falls back to the Fabric token's claims. Git Bash rewrites `/subscriptions/...` arguments (`MSYS_NO_PATHCONV=1`). PowerShell hooks must check `$LASTEXITCODE`.

## Safety notes

- The agent has **no write tools**. Agent-proposed writes (SAP notification, dispositions) must go through `actions/approval.py`. Its local-file store does not work inside a hosted-agent container and must be replaced by the Eventhouse-backed `approval_event` log before any write tool ships.
- No secrets are stored in the repo. `state.json`, `.env` and `.azure/` are git-ignored; auth is always your own `az login`.
- `scripts/simulate_telemetry.py` targeted the deleted hand-built Eventhouse and refuses to run without `--i-know-this-is-dev`.

## Next steps

Feed real downtime history (or generate labelled synthetic run-to-failure data) so `downtime_raw` and `prediction_stream` mean something; then train and shadow-score failure and remaining-useful-life models. Send a first real Teams message to confirm access through the private account, then surface Operations Agent alerts in Teams. Later: a Foundry IQ knowledge base for manuals and SOPs, approval-gated write tools, and optionally Wave 2.
