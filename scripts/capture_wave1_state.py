#!/usr/bin/env python
"""Write azd's captured Bicep outputs into state.json['wave1'].

`azd provision` (unlike infra/deploy.ps1, which reads `az deployment ...
--query properties.outputs` straight into state.json) has no built-in notion
of state.json — it keeps outputs in its own environment instead. The
Python setup/foundry scripts all read Wave 1 values via
`state.output("wave1", ...)`, so this bridges the two: run it right after
`azd provision`, before any setup script, and it's a no-op to re-run.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdmops.common.config import StateStore
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)

# azd also exposes its own AZURE_* environment bookkeeping vars alongside the
# actual Bicep outputs — only these are ones main.bicep declares as outputs.
WAVE1_OUTPUT_KEYS = [
    "location", "resourceGroupName", "vnetId", "vnetName", "agentSubnetId", "agentSubnetName",
    "peSubnetId", "peSubnetName", "containerSubnetId", "jumpboxContainerGroupName",
    "jumpboxPrincipalId", "fabricCapacityId", "fabricCapacityName", "keyVaultName",
    "keyVaultUri", "logAnalyticsWorkspaceId", "appInsightsConnectionString",
    "foundryAccountName", "foundryAccountId", "foundryAccountEndpoint",
    "foundryProjectName", "foundryProjectId",
]


def main() -> None:
    result = subprocess.run(["azd", "env", "get-values", "--output", "json"],
                             capture_output=True, text=True, check=True)
    values = json.loads(result.stdout)

    wave1 = {key: values[key] for key in WAVE1_OUTPUT_KEYS if key in values}
    missing = [key for key in WAVE1_OUTPUT_KEYS if key not in values]
    if missing:
        logger.warning("azd env didn't have these expected Wave 1 outputs: %s", missing)

    StateStore().merge("wave1", wave1)
    logger.info("Wrote %d Wave 1 outputs to state.json.", len(wave1))


if __name__ == "__main__":
    main()
