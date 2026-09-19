"""Local JSON-file-backed record store for approvals and incidents.

This is the demo/pilot backing store — good enough to run the whole
alert -> investigate -> approve -> act loop end to end without extra
infrastructure. The Foundry standard agent setup already provisions a Cosmos
DB account as one of its BYO resources; production hardening should move this
to a container there (shared, multi-writer state) rather than a local file
tied to one machine. Swap the two functions at the bottom (`_read`/`_write`)
for a Cosmos-backed implementation and everything above keeps working —
callers only see `RecordStore`.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".pdmops-data"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordStore:
    """One JSON file per collection (e.g. approvals.json, incidents.json), each
    holding {record_id: {...fields...}}."""

    def __init__(self, collection: str, base_dir: Path | str | None = None):
        base = Path(base_dir) if base_dir else _DEFAULT_DIR
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{collection}.json"
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def create(self, fields: dict[str, Any], record_id: str | None = None) -> str:
        record_id = record_id or str(uuid.uuid4())
        with self._lock:
            data = self._read()
            if record_id in data:
                raise ValueError(f"record {record_id} already exists in {self.path.name}")
            data[record_id] = {**fields, "id": record_id, "createdAt": utcnow_iso()}
            self._write(data)
        return record_id

    def update(self, record_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            if record_id not in data:
                raise KeyError(f"record {record_id} not found in {self.path.name}")
            data[record_id] = {**data[record_id], **fields, "updatedAt": utcnow_iso()}
            self._write(data)

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get(record_id)

    def list(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._read()
