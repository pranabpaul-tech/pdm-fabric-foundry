# Vendored module

Copied verbatim from the official Microsoft sample at:

`foundry-samples/infrastructure/infrastructure-setup-bicep/15-private-network-standard-agent-setup`

(local checkout: `C:\Users\pranabp\source\repos\foundry-samples`)

Do not hand-edit `main.bicep` or `modules-network-secured/*` here — if the upstream
sample changes, re-copy it and re-apply `fleet-ops.overrides.bicepparam` on top.
Our own wiring lives one level up in `infra/modules/foundry.bicep`, which calls this
vendored `main.bicep` as a module and passes in our VNet/subnet IDs.
