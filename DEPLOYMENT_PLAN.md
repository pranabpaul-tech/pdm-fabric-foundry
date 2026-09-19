# Predictive Maintenance on Fabric RTI + Foundry — Deployment Plan (Bicep + Python)

Scope: turn the Fabric Real-Time Manufacturing Jumpstart into the predictive-maintenance solution described in the solution plan (downtime fact, MTBF/MTTR/OEE, anomaly + failure + RUL models, Activator closed loop, Foundry-hosted agent).

Status legend used throughout: **[B]** Bicep, **[P]** Python, **[M]** manual / portal, **[V]** verify against current docs before building (preview or fast-moving API).

---

## 1. Design principle: who deploys what

Fabric items are **not ARM resources**. Only the Fabric *capacity* is. That splits the deployment three ways:

| Layer | Tool | What it owns |
|---|---|---|
| Azure platform | **Bicep** | Fabric capacity, Foundry account + project + model deployments, AI Search, ACR, Container Apps (RTI MCP host), Key Vault, Log Analytics / App Insights, managed identities, Azure RBAC, optional Event Grid MQTT broker, Logic App (SAP/Teams bridge) |
| Fabric items | **Python** (`fabric-cicd` for item definitions + thin REST client for orchestration) | Workspace, capacity assignment, workspace roles, Eventhouse/KQL DB, Lakehouse, Eventstreams, notebooks, pipelines, semantic model, reports, ML items, Activator, Data Agent |
| Data-plane config | **Python** (`azure-kusto-data`, notebook job runs) | KQL tables/policies/functions/materialized views, Lakehouse Delta DDL, seed dimensions, reason codes |
| Foundry agent runtime | **Python** (`azure-ai-projects`, `az acr build`) | Search index + RAG ingestion, tool connections, hosted-agent image + version, evaluations |
| Non-automatable | **Manual** | Tenant settings, Jumpstart install click-through, Activator rule authoring (until API covers it), Teams/SAP consent, Entra Agent ID approval |

Rules:
1. Bicep outputs feed Python via a `deploy/outputs/<env>.json` file. Python never hard-codes resource IDs.
2. Everything is idempotent (`create-or-alter`, `create-merge`, upsert-by-displayName). Re-running a stage must be safe.
3. No secrets. Managed identity / workload identity federation everywhere; deployment identity is a service principal with OIDC from CI.
4. Three environments: `dev` (small capacity, paused off-hours), `test`, `prod`. Same code, different `.bicepparam` + `parameter.yml`.

---

## 2. Repository layout

```
pdm-fabric-foundry/
├─ infra/                          # Bicep
│  ├─ main.bicep                   # subscription-scope orchestrator
│  ├─ modules/
│  │  ├─ fabric-capacity.bicep
│  │  ├─ foundry.bicep             # account + project + model deployments
│  │  ├─ foundry-agent-standard.bicep  # capability host, BYO Cosmos/Storage/Search (adapt from foundry-samples)
│  │  ├─ search.bicep
│  │  ├─ acr.bicep
│  │  ├─ containerapps.bicep       # env + RTI MCP server app
│  │  ├─ observability.bicep
│  │  ├─ keyvault.bicep
│  │  ├─ eventgrid-mqtt.bicep      # optional: managed MQTT broker for telemetry
│  │  ├─ logicapp-bridge.bicep     # Activator -> SAP PM / Teams
│  │  └─ rbac.bicep
│  └─ params/{dev,test,prod}.bicepparam
├─ fabric/                         # Fabric item definitions (git-integration format, consumed by fabric-cicd)
│  ├─ parameter.yml                # env-specific find/replace (workspace IDs, KQL URIs, lakehouse IDs)
│  ├─ eventstream_downtime.Eventstream/
│  ├─ nb_seed_dimensions.Notebook/
│  ├─ nb_gold_ddl.Notebook/
│  ├─ nb_feature_engineering.Notebook/
│  ├─ nb_train_failure_clf.Notebook/
│  ├─ nb_train_rul.Notebook/
│  ├─ nb_score_batch.Notebook/
│  ├─ pl_gold_refresh.DataPipeline/
│  ├─ pl_score_batch.DataPipeline/
│  ├─ sm_pdm_kpis.SemanticModel/
│  └─ rpt_pdm_trends.Report/
├─ kql/                            # KQL scripts, applied in filename order
│  ├─ 010_tables.kql
│  ├─ 020_functions.kql
│  ├─ 030_update_policies.kql
│  ├─ 040_materialized_views.kql
│  ├─ 050_policies_retention_cache.kql
│  ├─ 060_onelake_availability.kql
│  └─ 070_anomaly.kql
├─ agent/                          # Foundry hosted agent
│  ├─ Dockerfile
│  ├─ app/  (agent framework code, tools, prompts)
│  ├─ rag/  (OEM manuals, SOPs -> index)
│  └─ evals/ (datasets + evaluators)
├─ src/pdmctl/                     # Python deployment package
│  ├─ config.py  auth.py  fabric_client.py  kusto.py
│  ├─ stages/{s2_fabric_bootstrap,s4_foundation,s5_semantic,s6_ml,s7_closed_loop,s8_agent}.py
│  └─ cli.py                       # `pdmctl deploy --env dev --stage s4`
├─ tests/  (smoke + data-contract tests)
├─ .github/workflows/{infra.yml,fabric.yml,agent.yml}
└─ pyproject.toml
```

Python deps: `azure-identity`, `azure-kusto-data`, `azure-kusto-ingest`, `requests`, `tenacity`, `fabric-cicd`, `azure-ai-projects`, `azure-search-documents`, `azure-mgmt-*` as needed, `typer`, `pyyaml`. **[V]** pin versions after checking the current `azure-ai-projects` hosted-agent surface.

---

## 3. Stage plan (maps to the 20-week delivery plan)

| Stage | Week | Tool | Deploys | Gate to pass |
|---|---|---|---|---|
| **S0** Prereqs & tenant config | 0 | M | Tenant settings, deployment SP, OIDC, subscriptions, quotas | Checklist §4 signed |
| **S1** Azure platform | 1 | B | Capacity, Foundry, Search, ACR, ACA env, KV, monitoring, RBAC | `what-if` clean; outputs JSON emitted |
| **S2** Fabric bootstrap | 1 | P | Workspace → capacity, roles, folders, env json | Workspace reachable by SP + ops group |
| **S3** Jumpstart install + gap map | 1 | M (+P read-only) | Jumpstart items into S2 workspace **[V]** | Inventory diff vs. target schema written to `docs/gap-map.md` |
| **S4** Data foundation | 2–5 | P | Downtime/WO Eventstream, KQL schema, Lakehouse gold tables, dims, reason codes, OneLake availability, baseline freeze | Row-count + contract tests green; baseline snapshot immutable |
| **S5** KPI/semantic layer | 4–6 | P | Semantic model (MTBF/MTTR/OEE), report, RTI dashboard | Measures reconcile to hand-calculated sample within 0.5% |
| **S6** AI models | 6–10 | P | Feature/training/scoring notebooks, MLflow models, batch pipeline, in-KQL anomaly | Shadow mode writing `fact_prediction`; AI scorecard populated |
| **S7** Closed loop | 10–13 | B + P + M | Logic App bridge, Activator rules, Teams/SAP, disposition capture, prevention ledger | End-to-end synthetic alert produces notification + disposition row |
| **S8** Foundry agent | 12–16 | P (+B) | RAG index, MCP host, tool connections, hosted agent, evals | Eval thresholds met; write-tools blocked without approval |
| **S9** Promote & value | 16–20 | CI | dev→test→prod promotion, scale-out template | Finance-signed KPI formulas; runbook |

Deployment order inside a stage is fixed; across stages, S1 must precede everything, S4 precedes S5–S8.

---

## 4. S0 — Prerequisites (manual, once per tenant)

**Fabric tenant settings (Fabric admin portal)** **[V]** names shift:
- "Service principals can use Fabric APIs" → enable for the deployment-SP security group.
- "Users can create Fabric items" / Real-Time Intelligence enabled.
- Data Agent + Copilot / Azure OpenAI tenant switches on (and cross-geo processing if the capacity region lacks it).
- OneLake availability for KQL DB permitted.
- Activator, Data Science (ML models/experiments) enabled.

**Azure**
- Subscription(s): one per env or one shared with RGs per env. Register providers: `Microsoft.Fabric`, `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.OperationalInsights`, `Microsoft.EventGrid`, `Microsoft.Logic`.
- Model quota in the target region for the chosen chat model + embedding model (e.g. `gpt-4.1` / `text-embedding-3-large`) — request early, quota is the usual blocker.
- Capacity region = Foundry region where possible (latency + data-agent processing).

**Identities**
- `sp-pdm-deploy-<env>`: federated credential (GitHub OIDC). Roles: Contributor + User Access Administrator on the env RG (User Access Administrator needed for `rbac.bicep`), Fabric capacity admin, Fabric workspace Admin (added by S2).
- Entra groups: `grp-pdm-ops` (Viewer/Contributor), `grp-pdm-data` (Member), `grp-pdm-admin` (Admin), `grp-fabric-sp` (SPs allowed to call Fabric APIs).
- Agent identity: system-assigned MI of the Foundry project (Entra Agent ID if available in tenant **[V]**).

---

## 5. S1 — Bicep

### 5.1 `infra/main.bicep` (orchestrator)

```bicep
targetScope = 'subscription'

@allowed(['dev','test','prod'])
param env string
param location string = 'westeurope'
param workload string = 'pdm'
param fabricCapacitySku string
param fabricAdminMembers array           // UPNs or SP object IDs
param chatModel object                   // { name, version, capacity }
param embeddingModel object
param deployMqttBroker bool = false
param tags object = { workload: workload, env: env }

var suffix = '${workload}-${env}'
var rgName = 'rg-${suffix}'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module obs 'modules/observability.bicep' = {
  scope: rg
  name: 'obs'
  params: { suffix: suffix, location: location, tags: tags }
}

module kv 'modules/keyvault.bicep' = {
  scope: rg
  name: 'kv'
  params: { suffix: suffix, location: location, tags: tags }
}

module fabric 'modules/fabric-capacity.bicep' = {
  scope: rg
  name: 'fabric'
  params: {
    name: replace('fc${suffix}', '-', '')      // lowercase alnum only
    location: location
    sku: fabricCapacitySku
    adminMembers: fabricAdminMembers
    tags: tags
  }
}

module search 'modules/search.bicep' = {
  scope: rg
  name: 'search'
  params: { suffix: suffix, location: location, tags: tags }
}

module acr 'modules/acr.bicep' = {
  scope: rg
  name: 'acr'
  params: { name: replace('acr${suffix}', '-', ''), location: location, tags: tags }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  name: 'foundry'
  params: {
    suffix: suffix
    location: location
    chatModel: chatModel
    embeddingModel: embeddingModel
    appInsightsId: obs.outputs.appInsightsId
    searchId: search.outputs.id
    searchEndpoint: search.outputs.endpoint
    tags: tags
  }
}

module aca 'modules/containerapps.bicep' = {
  scope: rg
  name: 'aca'
  params: {
    suffix: suffix
    location: location
    logAnalyticsId: obs.outputs.logAnalyticsId
    acrName: acr.outputs.name
    tags: tags
  }
}

module mqtt 'modules/eventgrid-mqtt.bicep' = if (deployMqttBroker) {
  scope: rg
  name: 'mqtt'
  params: { suffix: suffix, location: location, tags: tags }
}

module bridge 'modules/logicapp-bridge.bicep' = {
  scope: rg
  name: 'bridge'
  params: { suffix: suffix, location: location, logAnalyticsId: obs.outputs.logAnalyticsId, tags: tags }
}

module rbac 'modules/rbac.bicep' = {
  scope: rg
  name: 'rbac'
  params: {
    foundryProjectPrincipalId: foundry.outputs.projectPrincipalId
    searchName: search.outputs.name
    acrName: acr.outputs.name
    mcpAppPrincipalId: aca.outputs.mcpPrincipalId
  }
}

// Consumed by the Python stages
output resourceGroup string = rg.name
output fabricCapacityName string = fabric.outputs.name
output foundryEndpoint string = foundry.outputs.endpoint
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output foundryProjectName string = foundry.outputs.projectName
output searchEndpoint string = search.outputs.endpoint
output acrLoginServer string = acr.outputs.loginServer
output mcpAppFqdn string = aca.outputs.mcpFqdn
output appInsightsConnectionString string = obs.outputs.connectionString
output logicAppTriggerUrlSecretName string = bridge.outputs.triggerSecretName
```

### 5.2 `modules/fabric-capacity.bicep`

```bicep
param name string
param location string
@allowed(['F2','F4','F8','F16','F32','F64','F128'])
param sku string
param adminMembers array
param tags object

resource cap 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku, tier: 'Fabric' }
  properties: {
    administration: { members: adminMembers }
  }
}

output name string = cap.name
output id string = cap.id
```

Sizing starting point **[V]**: dev `F8` (paused nights/weekends via Python `pdmctl capacity pause`), test `F16`, prod `F64` sized after shadow-mode telemetry load test. RTI + Spark streaming + Data Agent all draw from the same capacity; use separate capacities for prod vs. dev/test so experiments cannot throttle prod alerts.

### 5.3 `modules/foundry.bicep`

```bicep
param suffix string
param location string
param chatModel object       // { name: 'gpt-4.1', version: '2025-04-14', capacity: 100 }
param embeddingModel object  // { name: 'text-embedding-3-large', version: '1', capacity: 100 }
param appInsightsId string
param searchId string
param searchEndpoint string
param tags object

var acctName = 'aif-${suffix}'
var projName = 'proj-pdm-${take(uniqueString(resourceGroup().id), 4)}'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: acctName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  tags: tags
  properties: {
    allowProjectManagement: true          // enables Foundry projects on the account
    customSubDomainName: acctName
    publicNetworkAccess: 'Enabled'        // prod: 'Disabled' + private endpoints
    disableLocalAuth: true                // Entra-only
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Predictive Maintenance'
    description: 'PdM triage / RCA / shift-briefing agents'
  }
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'chat'
  sku: { name: 'GlobalStandard', capacity: chatModel.capacity }
  properties: { model: { format: 'OpenAI', name: chatModel.name, version: chatModel.version } }
}

resource embed 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'embedding'
  dependsOn: [chat]                       // serialise: model deployments on one account can't run in parallel
  sku: { name: 'GlobalStandard', capacity: embeddingModel.capacity }
  properties: { model: { format: 'OpenAI', name: embeddingModel.name, version: embeddingModel.version } }
}

// Project connections (Entra auth, no keys)
resource searchConn 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'conn-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    metadata: { ResourceId: searchId }
  }
}

resource aiConn 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'conn-appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    authType: 'ApiKey'                    // App Insights connection requires a credential; store via KV in prod
    credentials: { key: 'set-by-python-post-deploy' }
    metadata: { ResourceId: appInsightsId }
  }
}

output endpoint string = account.properties.endpoint
output projectName string = project.name
output projectEndpoint string = 'https://${acctName}.services.ai.azure.com/api/projects/${project.name}'
output projectPrincipalId string = project.identity.principalId
output accountId string = account.id
```

**[V]** API version/property names for `accounts/projects` and `connections` — check against the current Foundry Bicep reference. For **standard agent setup** (BYO Cosmos DB thread store, Storage, AI Search, capability host — required for hosted agents with data isolation and VNet), do **not** hand-write it: start from Microsoft's `foundry-samples` "standard agent setup" Bicep and vendor it as `modules/foundry-agent-standard.bicep`.

### 5.4 Other modules (contract only)

| Module | Key resources / notes |
|---|---|
| `search.bicep` | `Microsoft.Search/searchServices` Basic (dev) / Standard S1 (prod), semantic ranker on, `authOptions: aadOrApiKey` → Entra only after bootstrap, system MI |
| `acr.bicep` | `Microsoft.ContainerRegistry/registries` Standard, admin disabled, AcrPull for project MI + ACA MI (in `rbac.bicep`) |
| `containerapps.bicep` | Managed env wired to Log Analytics; one container app `ca-rti-mcp` (image `fabric-rti-mcp`, external ingress restricted, system MI) — this is the host for the **Fabric RTI MCP server**. Image is pushed by S8. Use a placeholder public image (`mcr.microsoft.com/k8se/quickstart`) on first deploy so Bicep succeeds before the image exists |
| `observability.bicep` | Log Analytics workspace + workspace-based App Insights; export `connectionString` |
| `keyvault.bicep` | RBAC-mode vault, soft-delete + purge protection; holds Logic App trigger URL, SAP creds (if any) |
| `eventgrid-mqtt.bicep` | `Microsoft.EventGrid/namespaces` with `topicSpacesConfiguration.state: 'Enabled'` — only if the customer has no MQTT broker; Fabric Eventstream/Python bridge consumes from it **[V]** which MQTT source option is GA in Eventstream |
| `logicapp-bridge.bicep` | Logic App (Consumption for dev, Standard for prod) with HTTP trigger called by Activator (via Power Automate/webhook) → Teams post + SAP PM notification via SAP connector (needs on-prem data gateway/ SAP ISU connectivity — flag as a dependency) |
| `rbac.bicep` | See matrix §5.5 |

### 5.5 Azure RBAC matrix (`rbac.bicep`)

| Principal | Scope | Role |
|---|---|---|
| Foundry project MI | Search | Search Index Data Reader, Search Service Contributor |
| Foundry project MI | ACR | AcrPull |
| Foundry project MI | Foundry account | Azure AI User |
| MCP Container App MI | ACR | AcrPull |
| Deployment SP | Search | Search Index Data Contributor (for RAG ingestion) |
| Deployment SP | Foundry project | Azure AI Project Manager / Azure AI User |
| `grp-pdm-ops` | Foundry project | Azure AI User (playground/eval read) |

### 5.6 Deploy commands

```powershell
az deployment sub what-if -l westeurope -f infra/main.bicep -p infra/params/dev.bicepparam
az deployment sub create  -l westeurope -f infra/main.bicep -p infra/params/dev.bicepparam `
  --query properties.outputs -o json > deploy/outputs/dev.json
```

`dev.bicepparam` example:

```bicep
using '../main.bicep'
param env = 'dev'
param fabricCapacitySku = 'F8'
param fabricAdminMembers = ['pranab.paul@gmail.com']   // + deployment SP object ID
param chatModel = { name: 'gpt-4.1', version: '2025-04-14', capacity: 50 }
param embeddingModel = { name: 'text-embedding-3-large', version: '1', capacity: 50 }
```

---

## 6. S2 — Fabric bootstrap (Python)

### 6.1 Fabric REST client (`src/pdmctl/fabric_client.py`)

```python
import base64, json, time
from typing import Any
import requests
from azure.identity import DefaultAzureCredential

FABRIC = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


class FabricClient:
    def __init__(self, cred=None):
        self.cred = cred or DefaultAzureCredential()
        self.s = requests.Session()

    def _h(self):
        return {"Authorization": f"Bearer {self.cred.get_token(SCOPE).token}",
                "Content-Type": "application/json"}

    def request(self, method: str, path: str, body: Any = None, ok=(200, 201, 202)):
        for attempt in range(6):
            r = self.s.request(method, f"{FABRIC}{path}", headers=self._h(),
                               data=json.dumps(body) if body is not None else None)
            if r.status_code == 429:                       # throttled
                time.sleep(int(r.headers.get("Retry-After", 5))); continue
            if r.status_code == 202:                       # long-running operation
                return self._poll(r)
            if r.status_code in ok:
                return r.json() if r.content else {}
            r.raise_for_status()
        raise RuntimeError(f"Throttled: {method} {path}")

    def _poll(self, r):
        loc = r.headers["Location"]
        wait = int(r.headers.get("Retry-After", 5))
        while True:
            time.sleep(wait)
            p = self.s.get(loc, headers=self._h())
            state = p.json().get("status") if p.content else None
            if p.status_code == 200 and state in (None, "Succeeded"):
                res = self.s.get(loc.rstrip("/") + "/result", headers=self._h())
                return res.json() if res.status_code == 200 and res.content else p.json()
            if state == "Failed":
                raise RuntimeError(p.text)

    # ---- helpers -------------------------------------------------------
    def find(self, ws_id: str, item_type: str, name: str):
        items = self.request("GET", f"/workspaces/{ws_id}/items?type={item_type}")["value"]
        return next((i for i in items if i["displayName"] == name), None)

    def ensure_item(self, ws_id, path_segment, item_type, name, payload=None):
        """Idempotent create for typed endpoints (eventhouses, lakehouses, ...)."""
        existing = self.find(ws_id, item_type, name)
        if existing:
            return existing
        body = {"displayName": name, **(payload or {})}
        return self.request("POST", f"/workspaces/{ws_id}/{path_segment}", body)

    @staticmethod
    def part(path: str, content: str | bytes):
        raw = content.encode() if isinstance(content, str) else content
        return {"path": path, "payload": base64.b64encode(raw).decode(), "payloadType": "InlineBase64"}
```

### 6.2 Bootstrap stage (`stages/s2_fabric_bootstrap.py`)

```python
from ..fabric_client import FabricClient

def run(cfg, outputs):
    fc = FabricClient()

    # 1. Fabric capacity GUID (differs from the ARM resource id)
    caps = fc.request("GET", "/capacities")["value"]
    cap = next(c for c in caps if c["displayName"].lower() == outputs["fabricCapacityName"].lower())
    if cap["state"] != "Active":
        raise SystemExit("Capacity paused - resume via ARM before continuing")

    # 2. Workspace (idempotent on displayName)
    ws = next((w for w in fc.request("GET", "/workspaces")["value"]
               if w["displayName"] == cfg.workspace_name), None)
    if not ws:
        ws = fc.request("POST", "/workspaces",
                        {"displayName": cfg.workspace_name, "capacityId": cap["id"]})
    else:
        fc.request("POST", f"/workspaces/{ws['id']}/assignToCapacity", {"capacityId": cap["id"]})

    # 3. Role assignments (groups, not individuals)
    for principal_id, ptype, role in cfg.workspace_roles:   # e.g. (grp-pdm-ops, "Group", "Viewer")
        fc.request("POST", f"/workspaces/{ws['id']}/roleAssignments",
                   {"principal": {"id": principal_id, "type": ptype}, "role": role},
                   ok=(200, 201, 409))                       # 409 = already assigned

    # The Foundry project MI / Entra agent identity is granted Viewer in S8 (least privilege)
    outputs["workspaceId"] = ws["id"]
    return outputs
```

Gate: `GET /workspaces/{id}` succeeds for the deployment SP and for a member of `grp-pdm-ops`.

---

## 7. S3 — Jumpstart install and gap map

1. **[M]** Install the Real-Time Manufacturing Jumpstart into the S2 workspace (~8 min). **[V]** whether the Jumpstart lets you target an existing workspace or creates its own; if it creates its own, either adopt that workspace (re-run S2 against it, keeping IaC ownership of roles/capacity) or move items. Decide once, in dev, then codify.
2. **[P]** Read-only inventory: `pdmctl inventory --env dev` lists items by type, dumps Eventhouse table schemas (`.show database schema as json`), Lakehouse tables, Data Agent config → `docs/inventory.json`.
3. Diff against the target schema (`kql/*.kql`, gold DDL) → `docs/gap-map.md`. Anything the Jumpstart already created (telemetry table, SAP master data tables, Data Agent `TalkToManufacturingData`) is **adopted**: our scripts use `create-merge`/`ALTER ... ADD COLUMNS` so they extend rather than replace.
4. **Go/no-go on the #1 risk**: do reason-coded downtime records exist at the customer? If not, S4 becomes a change-management stream — escalate now.

---

## 8. S4 — Data foundation (Python + KQL)

### 8.1 Order of operations

1. Eventhouse + KQL DB (adopt Jumpstart's or create `eh_pdm` / `kqldb_pdm`).
2. Apply `kql/*.kql` in order via Kusto client (below).
3. Lakehouse `lh_pdm` (schemas enabled) → run `nb_gold_ddl` (Delta DDL for gold tables) → run `nb_seed_dimensions` (dim_shift, dim_reason_code, dim_asset from SAP master).
4. Publish Eventstream `es_downtime_events` (fabric-cicd) with sources: operator/MES stop feed (Event Hubs / custom endpoint) and SAP work-order status changes; destinations: `downtime_raw` in KQL DB (+ Lakehouse for `fact_downtime_event` merge via pipeline).
5. OneLake shortcut in KQL DB → Lakehouse `dim_asset` (so update policy joins on the live dimension).
6. Enable OneLake availability on `telemetry_raw`, `telemetry_enriched`, `downtime_raw`.
7. Freeze the 12-month pre-AI baseline (§8.5).

### 8.2 KQL runner (`src/pdmctl/kusto.py`)

```python
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

def client(query_uri: str) -> KustoClient:
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
        query_uri, DefaultAzureCredential().get_token)   # adapt to the token-provider signature
    return KustoClient(kcsb)

def apply_scripts(kql_dir: str, query_uri: str, db: str):
    kc = client(query_uri)
    for f in sorted(Path(kql_dir).glob("*.kql")):
        # one command per block, blocks separated by a line with only `;;`
        for stmt in filter(None, (s.strip() for s in f.read_text().split("\n;;\n"))):
            kc.execute_mgmt(db, stmt)
        print(f"applied {f.name}")
```

Query/ingestion URIs come from `GET /workspaces/{ws}/kqlDatabases/{id}` → `properties.queryServiceUri`.

### 8.3 KQL scripts (essentials)

`010_tables.kql`
```kusto
.create-merge table telemetry_raw (asset_id:string, ts:datetime, vibration_rms:real, temp_c:real,
    current_a:real, pressure_bar:real, cycle_count:long, payload:dynamic)
;;
.create-merge table telemetry_enriched (asset_id:string, ts:datetime, vibration_rms:real, temp_c:real,
    current_a:real, pressure_bar:real, cycle_count:long, plant_id:string, line_id:string,
    asset_class:string, criticality:int)
;;
.create-merge table downtime_raw (event_id:string, asset_id:string, start_ts:datetime, end_ts:datetime,
    is_planned:bool, reason_code:string, detected_by:string, response_ts:datetime,
    repair_start_ts:datetime, repair_end_ts:datetime, cost_labour:real, cost_parts:real, lost_units:real)
;;
.create-merge table prediction_stream (asset_id:string, ts:datetime, model:string, horizon_h:int,
    p_failure:real, rul_h:real, rul_lo:real, rul_hi:real, run_id:string)
```

`020_functions.kql` + `030_update_policies.kql`
```kusto
.create-or-alter function with (folder='etl') fn_enrich_telemetry() {
    telemetry_raw
    | lookup kind=leftouter asset_dim on asset_id
    | project asset_id, ts, vibration_rms, temp_c, current_a, pressure_bar, cycle_count,
              plant_id, line_id, asset_class, criticality
}
;;
.alter table telemetry_enriched policy update
@'[{"IsEnabled":true,"Source":"telemetry_raw","Query":"fn_enrich_telemetry()","IsTransactional":false}]'
```
(`asset_dim` is the OneLake shortcut / external table onto `lh_pdm.dim_asset`; if shortcuts aren't available in your region, ingest a small `asset_dim` table with `.set-or-replace` from the seed notebook **[V]**.)

`040_materialized_views.kql`
```kusto
.create-or-alter materialized-view with (backfill=true) mv_asset_1m on table telemetry_enriched {
    telemetry_enriched
    | summarize avg_vib=avg(vibration_rms), max_vib=max(vibration_rms), avg_temp=avg(temp_c),
                avg_cur=avg(current_a), cycles=max(cycle_count), n=count()
      by asset_id, bin(ts, 1m)
}
;;
.create-or-alter materialized-view mv_asset_1h on materialized-view mv_asset_1m {
    mv_asset_1m | summarize avg_vib=avg(avg_vib), max_vib=max(max_vib), avg_temp=avg(avg_temp) by asset_id, bin(ts, 1h)
}
```
(If a view-on-view is rejected on your engine version, aggregate `mv_asset_1h` directly from `telemetry_enriched`.)

`050_policies_retention_cache.kql`
```kusto
.alter-merge table telemetry_raw policy retention softdelete = 90d recoverability = disabled
;;
.alter-merge table telemetry_enriched policy retention softdelete = 90d recoverability = disabled
;;
.alter table telemetry_enriched policy caching hot = 90d
;;
.alter-merge table downtime_raw policy retention softdelete = 3650d   // labelled history is the asset — keep it
```

`060_onelake_availability.kql` **[V]** exact syntax
```kusto
.alter-merge table telemetry_enriched policy mirroring dataformat=parquet with (IsEnabled=true, TargetLatencyInMinutes=15)
;;
.alter-merge table downtime_raw policy mirroring dataformat=parquet with (IsEnabled=true, TargetLatencyInMinutes=15)
```
Alternatively enable at database level through the portal toggle. Either way the Lakehouse side then reads via a OneLake shortcut — no copy.

`070_anomaly.kql` (real-time tier)
```kusto
.create-or-alter function with (folder='ml') fn_anomalies(lookback:timespan=6h) {
    mv_asset_1m
    | where ts > ago(lookback)
    | make-series v=avg(avg_vib) default=real(null) on ts step 1m by asset_id
    | extend (flag, score, baseline) = series_decompose_anomalies(v, 2.5, -1, 'linefit')
    | mv-expand ts to typeof(datetime), v to typeof(real), flag to typeof(int), score to typeof(real)
    | where flag != 0
}
```
Multivariate anomaly detection: run as a scheduled Fabric notebook/KQL query writing to `anomaly_events` if the in-Eventhouse multivariate option is not available in the region **[V]**.

### 8.4 Gold model (Lakehouse) — `nb_gold_ddl`

Delta tables (schema-enabled lakehouse, `gold` schema): `dim_asset`, `dim_shift`, `dim_reason_code`, `fact_downtime_event`, `fact_work_order`, `fact_production`, `fact_prediction`, `fact_intervention` (alert → disposition), `fact_prevention_ledger`. `fact_downtime_event` per the KPI backbone in the solution plan. Add constraints via data-contract tests rather than Delta CHECK (start simple):

- `duration_min = (end_ts - start_ts)`, `end_ts >= start_ts`
- `reason_code` ∈ `dim_reason_code`
- `is_planned` never null
- `repair_end_ts >= repair_start_ts >= response_ts` when present
- `fact_intervention.disposition` NOT NULL after 7 days → alert on breach (mandatory disposition)

Pipeline `pl_gold_refresh` merges `downtime_raw` (via OneLake) + SAP work orders into `fact_downtime_event` / `fact_work_order` on a schedule (15 min).

### 8.5 Freeze the baseline

Notebook `nb_freeze_baseline`: snapshot the trailing 12 months of `fact_downtime_event`, `fact_work_order`, `fact_production` into `gold_baseline.*` with a `baseline_version` + `frozen_at` and **immutability**: create in a separate schema, remove write permission for everyone except the deployment SP, store a hash of the tables in `docs/baseline-manifest.json` (committed). This is what finance signs.

### 8.6 Publishing items with fabric-cicd

```python
from fabric_cicd import FabricWorkspace, publish_all_items, unpublish_all_orphan_items

ws = FabricWorkspace(
    workspace_id=outputs["workspaceId"],
    environment=cfg.env,                              # selects block in fabric/parameter.yml
    repository_directory="fabric",
    item_type_in_scope=["Notebook", "DataPipeline", "Eventstream", "SemanticModel", "Report"],
)
publish_all_items(ws)
# unpublish_all_orphan_items(ws)   # ONLY in dev; never delete Jumpstart-owned items in prod
```

`fabric/parameter.yml` handles env-specific values (KQL query URI, lakehouse/eventhouse item IDs, Event Hub names): `find_replace` on placeholder tokens like `__KQL_URI__`. Item-ID injection requires items to exist first, so run in two passes: (1) create Eventhouse/KQL DB/Lakehouse with the REST client (§6.1) and write their IDs to outputs; (2) `publish_all_items`.

Typed create helpers (used in pass 1):

```python
eh = fc.ensure_item(ws, "eventhouses", "Eventhouse", "eh_pdm")
kdb = fc.ensure_item(ws, "kqlDatabases", "KQLDatabase", "kqldb_pdm",
        {"creationPayload": {"databaseType": "ReadWrite", "parentEventhouseItemId": eh["id"]}})
lh = fc.ensure_item(ws, "lakehouses", "Lakehouse", "lh_pdm",
        {"creationPayload": {"enableSchemas": True}})
```

Running a notebook (DDL / seed / freeze):

```python
def run_notebook(fc, ws, nb_id, params=None):
    return fc.request("POST",
        f"/workspaces/{ws}/items/{nb_id}/jobs/instances?jobType=RunNotebook",
        {"executionData": {"parameters": {k: {"value": v, "type": "string"} for k, v in (params or {}).items()}}})
    # returns after the LRO completes; assert final status == Completed
```

---

## 9. S5 — Semantic and KPI layer

- Semantic model `sm_pdm_kpis` (Direct Lake on `lh_pdm` gold + Direct Query/KQL for live state). Measures live in the model, not the report:
  `MTBF`, `MTTR_wrench`, `MTTR_total`, `Unplanned_h`, `Planned_h`, `Unplanned_pct`, `Cost_reactive|planned|predictive`, `Availability`, `Performance`, `Quality`, `OEE`, plus AI measures (`Precision_H`, `Recall_H`, `PR_AUC`, `FalseAlerts_per_asset_month`, `LeadTime_median`, `RUL_MAE`, `Prevented_h`, `Misses_h`, `Alerts_not_actioned`), all parameterised by a single `Horizon_H` table so H is stated everywhere.
- Deployed via fabric-cicd (TMDL definition in git). Post-deploy Python step: bind the Direct Lake connection to the env's lakehouse (parameter.yml) and trigger refresh.
- Report `rpt_pdm_trends` + RTI Real-Time Dashboard (KQL-backed) for live asset state.
- **Gate**: `tests/test_kpi_reconciliation.py` executes DAX (`POST /v1.0/myorg/groups/{ws}/datasets/{id}/executeQueries` via Power BI REST) for a fixed 3-asset × 1-month sample and compares with values calculated independently in pandas from the Delta tables (tolerance 0.5%). Ops and finance sign off on this test's expected values.

---

## 10. S6 — AI models

| Item | Deploy | Notes |
|---|---|---|
| In-Eventhouse anomaly | `070_anomaly.kql` (S4) + Activator/dashboard tile | Sub-second tier; precision measured vs. operator disposition |
| `nb_feature_engineering` | fabric-cicd | Windowed features (rolling mean/std/slope of vib/temp/current, cycles since last maintenance, asset age) read from OneLake copy of `telemetry_enriched` → `gold.features_asset_window` |
| `nb_train_failure_clf` | fabric-cicd + `run_notebook` | LightGBM/XGBoost, label = failure within H hours (H from config), **time-based split with gap ≥ H** (no leakage), class weights/focal, MLflow autolog, register model `pdm_failure_clf` |
| `nb_train_rul` | same | Survival (Weibull/Cox) or GBM-quantile regression → RUL + [lo, hi]; register `pdm_rul` |
| `nb_score_batch` + `pl_score_batch` | pipeline every 15 min | Loads models by alias `@champion`, writes `gold.fact_prediction` (+ `run_id`, `model_version`, `horizon_h`, `shadow=true`) |
| Real-time scoring (optional) | Spark Structured Streaming notebook writing `prediction_stream` **or** KQL inline `python()` plugin | Decide after shadow-mode latency needs are known; batch-15-min is acceptable for a 24–72 h horizon |
| Shadow mode | Config flag `shadow=true` in `fact_prediction` | Alerts are logged to `fact_intervention` with `channel='shadow'` and **do not** reach Activator actions. Flip per asset group after the 4–8 week window |
| AI scorecard | Semantic model AI measures (§9) | Live from day one of shadow |

Model promotion: notebook `nb_promote_model` moves alias `challenger → champion` only if PR-AUC ≥ champion and recall@precision 0.5 ≥ champion on the most recent held-out window. Store the decision in `gold.model_registry_log`.

---

## 11. S7 — Closed loop

1. **[B]** `logicapp-bridge.bicep` deployed in S1; here **[P]** wires its callback URL into Key Vault and the Activator action.
2. **[M→P]** Activator (Reflex) rules: source = `fact_prediction`/`prediction_stream`; condition `p_failure > threshold` for **N consecutive windows** per `asset_id`; actions:
   1. HTTP/Power Automate → Logic App → **SAP PM notification** (`QM/PM` notification create) with predicted failure mode, RUL, evidence link.
   2. Teams message to the duty-technician channel (adaptive card with *Confirm / False alarm / Already known / Not actioned* buttons).
   3. Insert `fact_intervention` row (alert_id, asset, p, rul, ts, `disposition = NULL`).
   Reflex definition can be created via the Items API `reflexes` with a definition payload, but authoring rules by hand in the portal and then **exporting the definition into git** is the reliable path today **[V]**. Codify by exporting → parameterise (`parameter.yml`) → publish with fabric-cicd.
3. **Mandatory disposition**: the adaptive-card response calls a Logic App endpoint that updates `fact_intervention.disposition`; a scheduled KQL/Spark check flags alerts >72 h without disposition and re-notifies the shift lead. `not_actioned` is a first-class value so the prevention ledger and false-positive rate stay honest.
4. `nb_prevention_ledger` (nightly): for `disposition = 'confirmed_tp'` with a completed intervention before the predicted window → compute `D_counterfactual` (asset+failure-mode history, with CI), `D_actual`, `attribution_factor`, write `fact_prevention_ledger`. Misses and not-actioned are computed in the same job.
5. Gate: synthetic alert (a test asset with injected telemetry) produces Teams card + SAP test-system notification + `fact_intervention` row; card click sets disposition; ledger row appears next run.

Rollout guard: Activator rules deployed with a `group filter` (pilot lines only) parameter; widen per env config.

---

## 12. S8 — Foundry hosted agent

### 12.1 Components and their deploy order

1. **RAG index** (Azure AI Search): index `pdm-docs` with vector field + semantic config; ingest OEM manuals, SOPs, past repair write-ups (chunk 500–800 tokens, metadata: asset_class, doc_type, source_uri). Python:

```python
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (SearchIndex, SimpleField, SearchableField, SearchField,
    SearchFieldDataType, VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, SemanticConfiguration, SemanticPrioritizedFields,
    SemanticField, SemanticSearch)

def ensure_index(endpoint, foundry_endpoint):
    idx = SearchIndex(
        name="pdm-docs",
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="asset_class", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="source_uri", type=SearchFieldDataType.String),
            SearchField(name="vec", type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                        searchable=True, vector_search_dimensions=3072, vector_search_profile_name="vp"),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[VectorSearchProfile(name="vp", algorithm_configuration_name="hnsw", vectorizer_name="vz")],
            vectorizers=[AzureOpenAIVectorizer(vectorizer_name="vz",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=foundry_endpoint, deployment_name="embedding", model_name="text-embedding-3-large"))]),
        semantic_search=SemanticSearch(configurations=[SemanticConfiguration(name="sem",
            prioritized_fields=SemanticPrioritizedFields(content_fields=[SemanticField(field_name="content")]))]),
    )
    SearchIndexClient(endpoint, DefaultAzureCredential()).create_or_update_index(idx)
```

2. **Fabric RTI MCP server** — build and push image, roll the Container App:

```powershell
az acr build -r $acr -t rti-mcp:$sha ./agent/mcp        # Dockerfile: pip install fabric-rti-mcp; run in HTTP/SSE mode  [V]
az containerapp update -n ca-rti-mcp -g $rg --image "$acr.azurecr.io/rti-mcp:$sha"
```
   The MCP app's managed identity needs **Viewer** on the Eventhouse (Fabric workspace role or KQL DB `viewers` principal) — grant via S8 Python (below). Restrict the MCP surface to *read* KQL (`query` tools) and disable ingest/management tools in config **[V]**. Front with API-key or Entra auth; do not expose anonymously.

3. **Fabric Data Agent**: the Jumpstart's `TalkToManufacturingData` is seeded, then re-pointed: add gold tables (`dim_asset`, `fact_downtime_event`, `fact_work_order`, `fact_prediction`, `fact_prevention_ledger`) and KQL tables to its data sources, set instructions + example queries, publish. Data Agent config is an item definition, so export into `fabric/` and fabric-cicd it **[V]** (item type/definition support for Data Agent; fall back to manual publish + export). Note: the Foundry Fabric tool authenticates as the **end user** (identity passthrough), so each caller needs at least Viewer on the workspace and read on the sources — plan the Entra group model accordingly and do not expect app-only calls to work **[V]**.

4. **Tool connections in the Foundry project** (Python, since they hold Fabric IDs):

```python
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

project = AIProjectClient(endpoint=outputs["foundryProjectEndpoint"], credential=DefaultAzureCredential())
# Connection to the Fabric data agent (workspace-id / artifact-id) and to the MCP server
# are created through the project connections API / ARM `connections` resource.  [V] exact category names:
#   Fabric data agent  -> category 'MicrosoftFabric'
#   RTI MCP server     -> remote MCP tool definition on the agent (server_url = https://<mcpAppFqdn>/mcp)
```

5. **SAP / work-order tool**: an OpenAPI tool fronting the Logic App (or an Azure Function) with two operations — `get_maintenance_history(asset_id)` (read, no approval) and `propose_notification(...)` (write → returns a *draft*, never posts). The **approval** happens in the technician UI/Teams card; only the approved call reaches SAP. Enforced in code, not prompts: the write operation requires an `approval_token` issued by the approval Logic App.

6. **Hosted agent**: container image with the agent code (Microsoft Agent Framework / Foundry Agents SDK **[V]**), tools registered:
   - `fabric_data_agent` (NL over gold + KQL)
   - `rti_mcp` (live KQL, read-only)
   - `sap_wo` OpenAPI tool (read + propose)
   - `azure_ai_search` (index `pdm-docs`)
   - Domain functions: `get_asset_evidence(asset_id)` (calls KQL for trend + similar-failure retrieval), `estimate_cost_of_waiting(asset_id, rul)`.

```powershell
az acr build -r $acr -t pdm-agent:$sha ./agent
```
```python
# deploy_agent.py  — hosted agents are preview; confirm current SDK call names  [V]
project = AIProjectClient(endpoint=outputs["foundryProjectEndpoint"], credential=DefaultAzureCredential())
agent = project.agents.create_version(                 # [V] name/shape of hosted-agent API
    agent_name="pdm-triage",
    definition={
        "kind": "hosted",
        "image": f"{outputs['acrLoginServer']}/pdm-agent:{sha}",
        "cpu": "1", "memory": "2Gi",
        "environment_variables": {
            "PROJECT_ENDPOINT": outputs["foundryProjectEndpoint"],
            "MODEL_DEPLOYMENT": "chat",
            "MCP_URL": f"https://{outputs['mcpAppFqdn']}/mcp",
            "SEARCH_INDEX": "pdm-docs",
        },
    },
)
```

7. **Scenario agents** — one hosted agent app exposing multiple entry points / or separate agent versions, in priority order: `pdm-triage` → `pdm-rca` (triggered from a Logic App/Event on unplanned-stop `downtime_raw` insert) → `pdm-shift-briefing` (scheduled at shift handover) → `pdm-metric-explainer`.

### 12.2 Fabric-side grants for the agent stack (Python)

```python
# Least privilege: Viewer only. Writes go through the approval-gated SAP tool, never directly to Fabric.
for pid, ptype in [(outputs["mcpPrincipalId"], "ServicePrincipal"),
                   (outputs["foundryProjectPrincipalId"], "ServicePrincipal")]:
    fc.request("POST", f"/workspaces/{ws}/roleAssignments",
               {"principal": {"id": pid, "type": ptype}, "role": "Viewer"}, ok=(200, 201, 409))
```
Tighten further with item-level permissions / KQL DB `.add database viewers` for the MCP identity, and OneLake data-access roles on the Lakehouse to limit the agent to gold schemas **[V]**.

### 12.3 Governance & observability

- Entra agent identity per agent; no shared keys; `disableLocalAuth: true` on the Foundry account.
- Tracing: App Insights connection on the project (Bicep) + OpenTelemetry in the agent app; all tool calls and approvals are traced with `asset_id`, `alert_id`, `user`.
- Content Safety / prompt-shield policy on the model deployments (`raiPolicyName`) **[V]**.
- Budget alerts on model deployments (Azure Cost Mgmt) and per-agent token quotas.

### 12.4 Evaluation harness (`agent/evals/`)

- Datasets (JSONL): 40–60 golden triage scenarios built from historical failures (frozen from the baseline), each with expected evidence items, expected action, and RUL/cost ground truth.
- Evaluators: groundedness, tool-call accuracy, task success (LLM-judge with rubric), **action acceptance rate** (from `fact_intervention` in shadow/live), safety (attempted un-approved write = hard fail).
- Runner in CI (`agent.yml`): `azure-ai-evaluation` (or Foundry evaluations API) on every agent image build; block promotion if groundedness < 0.85, task success < 0.80, or any unapproved-write attempt.
- Online: sample 5–10 % of production runs to the same evaluators; trend in the semantic model next to the operational KPIs.

---

## 13. CI/CD

Three workflows, OIDC login (`azure/login` with federated credential), env protection rules for `test`/`prod`:

| Workflow | Trigger | Steps |
|---|---|---|
| `infra.yml` | changes in `infra/**` | `bicep build` + `az deployment sub what-if` on PR (comment posted); `create` on merge to env branch; upload `outputs/<env>.json` as artifact |
| `fabric.yml` | changes in `fabric/**`, `kql/**`, `src/**` | lint (ruff), unit tests, `pdmctl deploy --stage s4..s7`, then smoke + KPI-reconciliation tests; **manual approval** before `prod` |
| `agent.yml` | changes in `agent/**` | `az acr build`, run evals against the candidate, create agent version, canary (10 % of shift-briefing runs) → promote |

Promotion order: dev (auto) → test (auto after green) → prod (approval). Fabric git integration on the dev workspace is optional; fabric-cicd from the repo is the source of truth so there is one direction of change.

---

## 14. Validation gates and rollback

| Check | Where | Rollback |
|---|---|---|
| Bicep `what-if` has no unexpected deletes | PR | Revert commit, redeploy (complete-mode not used) |
| KQL script idempotency (apply twice → no diff) | S4 test | Scripts are additive; use `.drop` scripts under `kql/rollback/` for dev only |
| Data-contract tests (§8.4) | nightly + post-deploy | Pause downstream pipelines (`pl_score_batch`), keep last-good gold snapshot |
| Baseline immutability (hash matches manifest) | nightly | Restore from Delta time-travel |
| Shadow-mode isolation (no shadow alert creates a SAP notification) | S7 test | Kill switch: single config row `alerts_enabled=false` read by the Activator condition |
| Agent write-safety test | CI | Roll agent version back (`create_version` with previous image tag) |
| Cost | Azure Cost alerts on capacity + model deployments | Pause capacity (`az resource invoke-action --action suspend`) — never in prod without on-call agreement |

---

## 15. Known gaps / things to verify before committing dates

1. **Jumpstart install mechanics** — target workspace behaviour and the exact installer (portal vs. `fabric-jumpstart` notebook package). Decide in week 1.
2. **Activator API coverage** — rule authoring may stay manual; plan an export-to-git workflow.
3. **Data Agent as code** — definition export/import and service-principal support are still maturing; assume portal publish + export for now.
4. **Foundry hosted agents** are preview: SDK method names, supported regions, and identity model may change; keep `deploy_agent.py` small and isolated.
5. **Fabric data agent tool needs user identity passthrough** — impacts who can talk to the agent and how scheduled agents (shift briefing) run; scheduled agents may need to go through the RTI MCP + a service identity instead of the data-agent tool.
6. **OneLake availability & shortcuts** — confirm regional support and the exact mirroring-policy syntax.
7. **SAP connectivity** — Logic App SAP connector needs an on-prem data gateway or SAP Cloud Connector path; a network/security lead is a dependency from week 2.
8. **Capacity throttling** — Spark streaming + RTI + Data Agent share CUs; load-test in test at the expected telemetry rate before prod sizing.
9. **Quota** — chat/embedding model quota in region; request in S0.

## 16. Immediate next actions (week 1)

1. Complete S0 checklist; request model quota; create deployment SP + groups.
2. Run S1 in `dev` (`what-if` → deploy), commit `deploy/outputs/dev.json` schema (no secrets).
3. Run S2, install Jumpstart (S3), produce `docs/inventory.json` and `docs/gap-map.md`.
4. Ask the customer for a sample of reason-coded downtime records — this decides whether S4 is a data or a change-management project.
