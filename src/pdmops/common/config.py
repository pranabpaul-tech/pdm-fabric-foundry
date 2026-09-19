"""Settings loaded from .env, and a small JSON-backed state store.

state.json is the seam between the Bicep waves (infra/deploy.ps1 writes wave1/
wave2/wave3 outputs into it) and the Python setup scripts (which write
workspace/eventhouse/eventstream/ops_agent/foundry_agent sections as they run).
Every setup script reads its inputs from here and writes its outputs back here,
which is what makes re-running a failed step safe — it picks up where the
previous successful step left off instead of redoing everything.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PDMOPS_", env_file=".env", extra="ignore")

    subscription_id: str = ""
    resource_group: str = "rg-pdm-fabric-foundry"
    location: str = "swedencentral"

    fabric_capacity_name: str = "pdmopsf8"
    workspace_name: str = "pdm-fabric-foundry"

    eventhouse_name: str = "pdmops-eventhouse"
    kql_database_name: str = "pdmops-kql-db"
    table_name: str = "telemetry_enriched"
    raw_table_name: str = "telemetry_raw"

    eventstream_name: str = "es-pdm-downtime"

    ops_agent_name: str = "PdM Operations Monitor"
    teams_channel_webhook_id: str = ""

    foundry_account_name: str = ""
    foundry_project_name: str = "pdm-agents"
    # Technical identifier used as the actual Fabric/Foundry item name (no
    # spaces) — set interactively by infra/hooks/preprovision.ps1|.sh into
    # .env. Read by foundry/deploy_hosted_agent.py; flows into state.json
    # from there, so publish_teams.py and everything downstream picks it up
    # without needing this setting itself.
    foundry_agent_name: str = "pdm-orchestrator"

    key_vault_uri: str = ""

    state_file: str = "state.json"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


class StateStore:
    """Thread-safe read/merge/write over a single JSON file.

    Sections are merged shallowly at the top level (state[section] = {**old, **new})
    so a script that only knows a couple of new fields doesn't clobber fields a
    previous script wrote into the same section.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or get_settings().state_file)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def get(self, section: str, default: Any = None) -> Any:
        with self._lock:
            return self._read().get(section, default)

    def require(self, section: str, *keys: str) -> dict[str, Any]:
        """Read a section and raise a clear error naming which upstream script to run."""
        data = self.get(section)
        if not data:
            raise RuntimeError(
                f"state.json has no '{section}' section yet. "
                f"Run the setup script that produces it before this one."
            )
        missing = [k for k in keys if k not in data]
        if missing:
            raise RuntimeError(
                f"state.json['{section}'] is missing {missing} — "
                f"re-run the script that populates '{section}', it may have failed partway through."
            )
        return data

    def merge(self, section: str, values: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data[section] = {**data.get(section, {}), **values}
            self._write(data)

    def all(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def output(self, section: str, key: str) -> Any:
        """Read one value from a Bicep-outputs-shaped section.

        deploy.ps1 writes `az deployment ... --query properties.outputs` straight
        into state.json, so each value is `{"value": ..., "type": "..."}` rather
        than a bare scalar. This unwraps that shape (and tolerates a bare scalar
        too, for sections a Python script wrote directly).
        """
        section_data = self.require(section, key)
        raw = section_data[key]
        return raw["value"] if isinstance(raw, dict) and "value" in raw else raw
