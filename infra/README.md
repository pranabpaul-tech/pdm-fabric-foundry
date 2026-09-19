# PdM Fabric Foundry — infra

Wave 0 plus three Bicep waves, because two of them need IDs that don't exist until a
Python step or a human portal step has run. See the repo root README for the
full deployment walkthrough (including the `azd` path); this file is the
infra-specific reference.

## Wave 1 — `main.bicep`

Network (VNet + 3 subnets), F8 Fabric capacity, Key Vault, Log Analytics +
App Insights, a container-based jumpbox (`modules/aci-jumpbox.bicep`), and the
VNet-injected Foundry account/project (vendored from `modules/foundry-vendored/`,
the real Microsoft sample at
`foundry-samples/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup`
— don't hand-edit anything under `foundry-vendored/`, see its own
`VENDORED_FROM.md`).

**Before deploying:** fill in `main.bicepparam` (Fabric admin UPNs, your
operator object ID, and the resource IDs of an existing AI Search / Storage /
Cosmos DB account — the Foundry standard agent setup refuses to build
without all three; see `wave0-byo-resources.bicep` if you need to create
them).

```powershell
./deploy.ps1 -Wave 1
```

or, via `azd` (from the repo root — runs Wave 1 and the Fabric pipeline
setup scripts automatically, see the root README):

```bash
azd provision
```

If the Foundry capability host doesn't come up automatically as part of the
deployment, run `foundry-vendored/createCapHost.sh`; check first with:

```bash
az rest --method get --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>/capabilityHosts?api-version=2025-04-01-preview"
```

## Wave 2 — `wave2-fabric-privatelink.bicep`

Needs `workspaceId` from `state.json` (`setup/01_workspace.py` writes it).
Run `setup/01_workspace.py` through `04_eventstream.py` first, **then**:

```powershell
./deploy.ps1 -Wave 2
```

Verify before locking anything down — `python -m pdmops.validate.network_check`
from the jumpbox should show the workspace FQDN resolving to a private IP. A
fresh capacity can take up to 24 hours to appear in the private DNS zone; a
failure here in the first day usually means "wait," not "broken."

## Wave 3 — `wave3-bot.bicep`

Needs `msaAppId` and `activityEndpoint` from `state.json['foundry_agent']`
(`foundry/deploy_hosted_agent.py` writes them). Run that, exercise the agent
in the Foundry playground, **then**:

```powershell
./deploy.ps1 -Wave 3
```

Then `python -m pdmops.foundry.publish_teams` to actually publish it —
Bicep only creates the Bot Service resource; the PATCH and
`microsoft365/publish` REST calls it depends on live in that script.

## Prerequisites Bicep can't do for you (Fabric admin portal, manual)

- Register resource providers: `Microsoft.Fabric`, `Microsoft.BotService`,
  `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.Search`,
  `Microsoft.DocumentDB`, `Microsoft.Storage`, `Microsoft.KeyVault`.
  Re-register `Microsoft.Fabric` again the first time you use
  *workspace*-level private link — it has its own registration flag.
- Fabric admin portal → tenant settings: enable **Azure Private Link**
  (~15 min to propagate) and **Configure workspace-level inbound network
  rules**, and whatever Copilot / Azure OpenAI tenant settings the
  Operations Agent needs.
- Pick a region that isn't `eastus` — the Operations Agent isn't available
  there — and that's co-regional with your Foundry model.

## Notes on a couple of API shapes that are thinly documented upstream

- **Operations Agent's `OperationsAgentV1` definition schema.**
  `setup/06_ops_agent.py` uses an author-in-portal, capture-the-definition
  workflow rather than guessing the shape, since the real definition part
  name and structure aren't obvious from the public docs alone.
- **The downtime Eventstream definition.** Same capture workflow - author it once in the
  portal, then `setup/04_eventstream.py --capture <id>` (see `artifacts/eventstream.downtime.definition.json`).
  The telemetry Eventstream belongs to the Real-Time Manufacturing Jumpstart; `artifacts/kql/` adds the
  enrichment update policy on top of whatever table it lands in.
