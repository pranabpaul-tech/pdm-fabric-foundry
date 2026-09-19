"""Fabric REST API client: bearer auth, long-running-operation polling, and the
base64 definition-part helper every item-creation script needs.

Getting the LRO polling right here is what keeps setup/02-06 from being flaky —
Fabric item creates return 202 with a Location/Retry-After header, not the
created item inline, and skipping the poll makes every downstream ID lookup a
race condition.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from .auth import FABRIC_SCOPE, get_token_provider
from .logging_setup import setup_logging

logger = setup_logging(__name__)

FABRIC_API_ROOT = "https://api.fabric.microsoft.com/v1"


class FabricApiError(RuntimeError):
    def __init__(self, response: requests.Response):
        self.status_code = response.status_code
        self.body = _safe_json(response)
        super().__init__(f"Fabric API error {response.status_code} for {response.request.method} "
                          f"{response.request.url}: {json.dumps(self.body)[:2000]}")


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:2000]}


@dataclass
class LroResult:
    status: str
    body: dict[str, Any]


class FabricClient:
    """Thin wrapper over requests.Session with Fabric bearer auth and LRO polling."""

    def __init__(self, timeout: float = 30.0, max_poll_seconds: float = 600.0):
        self._tokens = get_token_provider()
        self._session = requests.Session()
        self._timeout = timeout
        self._max_poll_seconds = max_poll_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tokens.get_token(FABRIC_SCOPE)}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, json_body: Any = None, *, base: str = FABRIC_API_ROOT) -> requests.Response:
        url = path if path.startswith("http") else f"{base}{path}"
        resp = self._session.request(method, url, headers=self._headers(), json=json_body, timeout=self._timeout)
        if resp.status_code >= 400:
            raise FabricApiError(resp)
        return resp

    def grant_workspace_role(self, workspace_id: str, principal_id: str, principal_type: str, role: str) -> None:
        """Idempotent — Fabric returns 200 for a principal that already has
        this role rather than erroring."""
        self.call("POST", f"/workspaces/{workspace_id}/roleAssignments", {
            "principal": {"id": principal_id, "type": principal_type},
            "role": role,
        })

    def call(self, method: str, path: str, json_body: Any = None, *, base: str = FABRIC_API_ROOT) -> dict[str, Any]:
        """Call an endpoint and transparently follow the LRO pattern if it returns 202."""
        resp = self.request(method, path, json_body, base=base)

        if resp.status_code != 202:
            return _safe_json(resp) if resp.content else {}

        operation_url = resp.headers.get("Location")
        if not operation_url:
            # Not every Fabric 202 is a pollable LRO — assignToCapacity, confirmed
            # live, returns 202 with no Location header and is already effectively
            # applied by the time the response comes back. Treat "202, no
            # Location" as synchronous-done rather than a hard error.
            logger.debug("202 from %s %s had no Location header — treating as synchronously complete.", method, path)
            return _safe_json(resp) if resp.content else {}
        retry_after = float(resp.headers.get("Retry-After", "5"))

        result = self._poll(operation_url, retry_after)
        if result.status != "Succeeded":
            raise RuntimeError(f"Fabric long-running operation for {method} {path} ended in status "
                                f"'{result.status}': {json.dumps(result.body)[:2000]}")

        # A completed operation's own response commonly carries a second Location
        # header pointing at the actual result (e.g. the created item). Fall back
        # to the operation body itself if there isn't one.
        final_resp = self._session.get(operation_url, headers=self._headers(), timeout=self._timeout)
        result_url = final_resp.headers.get("Location")
        if result_url:
            result_resp = self._session.get(result_url, headers=self._headers(), timeout=self._timeout)
            if result_resp.status_code < 400 and result_resp.content:
                return _safe_json(result_resp)

        return result.body

    def _poll(self, operation_url: str, initial_interval: float) -> LroResult:
        deadline = time.monotonic() + self._max_poll_seconds
        interval = max(initial_interval, 2.0)

        while True:
            resp = self._session.get(operation_url, headers=self._headers(), timeout=self._timeout)
            if resp.status_code >= 400:
                raise FabricApiError(resp)
            body = _safe_json(resp)
            status = body.get("status", "Unknown")
            logger.debug("LRO %s -> %s", operation_url, status)

            if status in ("Succeeded", "Failed", "Cancelled"):
                return LroResult(status=status, body=body)

            if time.monotonic() > deadline:
                raise TimeoutError(f"Fabric operation at {operation_url} did not complete within "
                                    f"{self._max_poll_seconds}s (last status: {status})")

            time.sleep(interval)
            interval = min(interval * 1.5, 30.0)  # gentle backoff, capped


def definition_part(path: str, obj: dict[str, Any]) -> dict[str, str]:
    """Encode one file of an item's definition as Fabric's InlineBase64 part shape."""
    payload = base64.b64encode(json.dumps(obj, indent=2).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def decode_definition_part(part: dict[str, Any]) -> dict[str, Any]:
    """Inverse of definition_part — used by --capture modes to read a definition back."""
    if part.get("payloadType") != "InlineBase64":
        raise ValueError(f"Unsupported payloadType: {part.get('payloadType')}")
    raw = base64.b64decode(part["payload"])
    return json.loads(raw)
