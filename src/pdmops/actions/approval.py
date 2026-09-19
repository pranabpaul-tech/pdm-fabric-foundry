"""The approval gate every action tool must pass through.

The Foundry agent's instructions require it to request explicit approval
before calling an action tool, and to never claim an action succeeded unless
the tool actually returned success. This module is the enforcement point for
both: `request_approval()` is the tool the agent calls to ask, and
`require_approved()` is what `power_automate.py` / `fabric_notebook.py` call
before doing anything — it raises rather than silently proceeding if there's
no matching, unexpired, approved token.

Approvals expire after 3 days, mirroring the platform's own semantics for
operations pending human action — see the Open Risks section of the design
plan (risk 11). A stale approval card left open in a demo tenant should fail
loudly, not silently do nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from .incident_store import RecordStore, utcnow_iso

APPROVAL_TTL = timedelta(days=3)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalError(RuntimeError):
    pass


def _store() -> RecordStore:
    return RecordStore("approvals")


def request_approval(action_type: str, description: str, payload: dict, requested_by: str = "pdm-orchestrator") -> str:
    """Called by the agent's action tool wrapper before doing anything
    disruptive. Returns an approval_id the operator (in Teams) then approves
    or denies; the actual action tool call blocks on require_approved()."""
    store = _store()
    expires_at = (datetime.now(timezone.utc) + APPROVAL_TTL).isoformat()
    approval_id = store.create({
        "actionType": action_type,
        "description": description,
        "payload": payload,
        "requestedBy": requested_by,
        "status": ApprovalStatus.PENDING.value,
        "expiresAt": expires_at,
    })
    return approval_id


def _effective_status(record: dict) -> ApprovalStatus:
    status = ApprovalStatus(record["status"])
    if status == ApprovalStatus.PENDING and record.get("expiresAt"):
        if datetime.fromisoformat(record["expiresAt"]) < datetime.now(timezone.utc):
            return ApprovalStatus.EXPIRED
    return status


def decide(approval_id: str, approved: bool, decided_by: str) -> None:
    """Called from the Teams approval flow (or a validate/ test harness) when
    an operator responds."""
    store = _store()
    record = store.get(approval_id)
    if record is None:
        raise ApprovalError(f"No such approval: {approval_id}")
    if _effective_status(record) != ApprovalStatus.PENDING:
        raise ApprovalError(f"Approval {approval_id} is no longer pending (status={_effective_status(record).value})")
    store.update(approval_id, {
        "status": (ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED).value,
        "decidedBy": decided_by,
        "decidedAt": utcnow_iso(),
    })


def require_approved(approval_id: str) -> dict:
    """Raises ApprovalError unless approval_id resolves to a currently-approved,
    unexpired approval. Every action tool must call this first — see
    actions/power_automate.py and actions/fabric_notebook.py."""
    store = _store()
    record = store.get(approval_id)
    if record is None:
        raise ApprovalError(f"No such approval: {approval_id}")
    status = _effective_status(record)
    if status != ApprovalStatus.APPROVED:
        raise ApprovalError(f"Approval {approval_id} is not approved (status={status.value}) — refusing to act.")
    return record
