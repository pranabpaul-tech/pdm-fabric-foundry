"""One credential, three token scopes.

Everything in this project runs interactively from the jumpbox under the
operator's own Entra identity — never a service principal secret. That's not
just a security preference: the Operations Agent (setup/06_ops_agent.py) can
only be created under a delegated (human) identity, and the workspace network
lockdown (setup/05_network_policy.py) is dangerous enough that it should always
have a human's fingerprints on it. `assert_delegated_identity()` fails fast if
a script is accidentally run under an app-only token.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import os

from azure.identity import AzureCliCredential, DefaultAzureCredential
from azure.core.credentials import AccessToken

FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
AZURE_MGMT_SCOPE = "https://management.azure.com/.default"
AI_FOUNDRY_SCOPE = "https://ai.azure.com/.default"
KEYVAULT_SCOPE = "https://vault.azure.net/.default"


@dataclass
class _CachedToken:
    token: AccessToken
    scope: str


class TokenProvider:
    """Caches one AccessToken per scope and refreshes ~5 minutes before expiry."""

    _REFRESH_SKEW_SECONDS = 300

    def __init__(self):
        # On the ACI jumpbox, DefaultAzureCredential silently picks the
        # container's own system-assigned managed identity (always available
        # via IMDS) *before* ever trying AzureCliCredential — so `az login` as
        # a real human has no effect on which identity Python code actually
        # gets, even after a real interactive sign-in. Hit this as a live
        # 400 (AADSTS500016, OBO not supported for the MI) on Teams publish,
        # which specifically requires a delegated human token. Set
        # PDMOPS_FORCE_CLI_CREDENTIAL=1 to force AzureCliCredential and
        # bypass the managed identity for steps that need a real user.
        if os.environ.get("PDMOPS_FORCE_CLI_CREDENTIAL") == "1":
            self._credential = AzureCliCredential()
        else:
            self._credential = DefaultAzureCredential(
                exclude_shared_token_cache_credential=True,
            )
        self._cache: dict[str, _CachedToken] = {}

    def get_token(self, scope: str) -> str:
        cached = self._cache.get(scope)
        now = time.time()
        if cached is None or cached.token.expires_on - now < self._REFRESH_SKEW_SECONDS:
            token = self._credential.get_token(scope)
            self._cache[scope] = _CachedToken(token=token, scope=scope)
            return token.token
        return cached.token.token

    def get_credential(self) -> DefaultAzureCredential:
        return self._credential


_provider: TokenProvider | None = None


def get_token_provider() -> TokenProvider:
    global _provider
    if _provider is None:
        _provider = TokenProvider()
    return _provider


def get_tenant_id() -> str:
    """Shell out to `az account show` — reused by anything that needs to build
    a KQL `aadapp=<appId>;<tenantId>` viewer principal string."""
    import shutil
    import subprocess

    az_path = shutil.which("az")
    if az_path is None:
        raise RuntimeError("Azure CLI not found on PATH.")
    result = subprocess.run(
        [az_path, "account", "show", "--query", "tenantId", "-o", "tsv"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Could not resolve tenant ID via az CLI: {result.stderr.strip()}")
    return result.stdout.strip()


def assert_delegated_identity() -> None:
    """Raise if the current credential resolves to an app-only (service principal)
    identity rather than a human's delegated one.

    Uses the Azure CLI identity directly, since that's the one path on the jumpbox
    that's unambiguously a signed-in human — az ad signed-in-user show fails for a
    service principal.
    """
    try:
        import shutil
        import subprocess

        # On Windows, `az` is an `az.cmd` shim — subprocess.run(["az", ...])
        # without shell=True fails with WinError 2 (CreateProcess doesn't do
        # PATHEXT resolution the way a shell does). shutil.which resolves the
        # real executable cross-platform; hit this as a live bug, not a guess.
        az_path = shutil.which("az")
        if az_path is None:
            raise FileNotFoundError("az")

        result = subprocess.run(
            [az_path, "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(
                "Not signed in as a delegated (human) identity. This step must run "
                "under an operator's own `az login` session, not a service principal "
                "— the Operations Agent inherits its creator's identity. "
                f"az CLI said: {result.stderr.strip()}"
            )
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI not found on PATH — required to verify a delegated identity.") from exc
