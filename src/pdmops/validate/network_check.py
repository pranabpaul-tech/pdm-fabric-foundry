#!/usr/bin/env python
"""Confirm the private-link DNS names resolve to private IPs. Run this from the
jumpbox, not from any machine outside the VNet — that's the whole point.

Checks (see infra/README.md phase 2 and 4):
  - tenant-level Fabric PL: {tenantId-no-hyphens}-api.privatelink.analysis.windows.net
  - workspace-level Fabric PL: {workspaceId-no-hyphens}.z{xy}.w.api.fabric.microsoft.com
  - Key Vault PE: {vaultName}.vault.azure.net

A public IP here doesn't necessarily mean something's broken — it can also
mean DNS just hasn't propagated yet (up to 24h for a fresh capacity, up to 30
min for a workspace network policy change). Re-run before assuming failure.
"""
from __future__ import annotations

import argparse
import ipaddress
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pdmops.common.config import StateStore
from pdmops.common.logging_setup import setup_logging

logger = setup_logging(__name__)


def resolve(hostname: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(hostname, None)})
    except socket.gaierror as exc:
        logger.error("DNS resolution failed for %s: %s", hostname, exc)
        return []


def is_private(ip: str, vnet_prefix: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr in ipaddress.ip_network(vnet_prefix) or addr.is_private


def check(name: str, hostname: str, vnet_prefix: str) -> bool:
    ips = resolve(hostname)
    if not ips:
        logger.error("[FAIL] %s (%s): did not resolve", name, hostname)
        return False
    ok = all(is_private(ip, vnet_prefix) for ip in ips)
    level = logger.info if ok else logger.error
    level("[%s] %s (%s) -> %s", "PASS" if ok else "FAIL", name, hostname, ips)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vnet-prefix", default="10.10.0.0/16")
    args = parser.parse_args()

    state = StateStore()
    all_data = state.all()

    results: list[bool] = []

    tenant_id = all_data.get("tenant", {}).get("tenantId")
    if tenant_id:
        tenant_host = f"{tenant_id.replace('-', '')}-api.privatelink.analysis.windows.net"
        results.append(check("tenant Fabric PL", tenant_host, args.vnet_prefix))
    else:
        logger.warning("No tenantId recorded in state.json under 'tenant' — pass it manually or skip this check.")

    workspace = all_data.get("workspace", {})
    workspace_id = workspace.get("workspaceId")
    if workspace_id:
        no_dash = workspace_id.replace("-", "")
        # {xy} = first two characters of the workspace object ID, per Microsoft's own nslookup example.
        workspace_host = f"{no_dash}.z{no_dash[:2]}.w.api.fabric.microsoft.com"
        results.append(check("workspace Fabric PL", workspace_host, args.vnet_prefix))

    kv_name = all_data.get("wave1", {}).get("keyVaultName")
    if kv_name:
        kv_name_value = kv_name["value"] if isinstance(kv_name, dict) else kv_name
        results.append(check("Key Vault PE", f"{kv_name_value}.vault.azure.net", args.vnet_prefix))

    if not results:
        logger.error("Nothing to check — state.json doesn't have workspace/wave1 data yet.")
        sys.exit(2)

    if not all(results):
        sys.exit(1)
    logger.info("network_check: PASS (%d/%d)", len(results), len(results))


if __name__ == "__main__":
    main()
