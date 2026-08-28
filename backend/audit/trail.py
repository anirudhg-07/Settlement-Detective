"""The audit trail: hashing, reconstruction, and verification.

Every investigation leaves a record that a finance team - or an auditor who
does not trust the finance team - can read end to end: what was asked, which
tools ran with which arguments, what came back, what was concluded, which
records were cited, and exactly why the case scored what it did.

**Tamper evidence, not tamper proofing.** Each step commits to the one before
it, so editing, deleting or reordering a row breaks the chain from that point
and the break is locatable. That defeats a quiet edit. It does not defeat
someone who rewrites every hash from the tampered step onward - for that you
need the chain head anchored somewhere outside this database, which is a
production concern and is deliberately not pretended at here.

Combined with the grants (the agent holds INSERT and no UPDATE or DELETE on
`investigation_steps`), the practical guarantee is: the system that writes the
trail cannot alter it, and anyone else who does will be seen.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from backend.money import format_paise

#: Identifiers the generator mints, e.g. `pay_9fK2xQ7bLm3aWd` - a known
#: prefix and exactly 14 base62 characters. Matching on the prefix alone also
#: swept up JSON field names like `fee_deducted`, which are not records.
RECORD_ID = re.compile(r"^(?:pay|order|fee|rfnd|adj|setl|si|cust)_[A-Za-z0-9]{14}$")


def canonical(payload: Any) -> str:
    """Stable JSON so a hash depends on content, not on dict ordering."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def genesis_hash(investigation_id: str) -> str:
    return hashlib.sha256(f"genesis:{investigation_id}".encode()).hexdigest()


def step_hash(prev_hash: str, step: dict) -> str:
    """Commit to this step's content *and* to everything before it."""
    body = canonical(
        {
            "investigation_id": step.get("investigation_id"),
            "seq": step.get("seq"),
            "step_type": step.get("step_type"),
            "tool_name": step.get("tool_name"),
            "tool_args": step.get("tool_args"),
            "tool_result": step.get("tool_result"),
            "observation": step.get("observation"),
        }
    )
    return hashlib.sha256(f"{prev_hash}|{body}".encode()).hexdigest()


def chain_steps(investigation_id: str, steps: list[dict]) -> list[dict]:
    """Stamp an ordered list of step rows with their hash chain."""
    prev = genesis_hash(investigation_id)
    for step in sorted(steps, key=lambda s: s["seq"]):
        step["prev_hash"] = prev
        step["content_hash"] = step_hash(prev, step)
        prev = step["content_hash"]
    return steps


def extract_record_ids(payload: Any) -> set[str]:
    """Every financial record identifier appearing anywhere in a tool result."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if RECORD_ID.match(node):
                found.add(node)
        elif isinstance(node, dict):
            # Values only. Keys are field names, not records.
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    return found


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


@dataclass
class ChainCheck:
    investigation_id: str
    steps_checked: int
    intact: bool
    broken_at: int | None = None
    detail: str = ""


def verify_chain(conn: Connection, investigation_id: str) -> ChainCheck:
    """Recompute the chain and report the first step that does not match."""
    rows = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT investigation_id, seq, step_type, tool_name, tool_args,"
                "       tool_result, observation, prev_hash, content_hash"
                "  FROM recon.investigation_steps"
                " WHERE investigation_id = :i ORDER BY seq"
            ),
            {"i": investigation_id},
        ).mappings()
    ]
    if not rows:
        return ChainCheck(investigation_id, 0, False, None, "no steps recorded")

    prev = genesis_hash(investigation_id)
    for row in rows:
        if row["prev_hash"] != prev:
            return ChainCheck(
                investigation_id, len(rows), False, row["seq"],
                "step does not follow the one before it - a row was inserted, "
                "removed or reordered",
            )
        expected = step_hash(prev, row)
        if row["content_hash"] != expected:
            return ChainCheck(
                investigation_id, len(rows), False, row["seq"],
                "step content does not match its hash - this row was edited "
                "after it was written",
            )
        prev = row["content_hash"]
    return ChainCheck(investigation_id, len(rows), True, None, "chain intact")


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------


@dataclass
class AuditTrail:
    """One investigation, reassembled. This is what the UI timeline renders."""

    exception: dict
    investigation: dict
    steps: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    chain: ChainCheck | None = None

    @property
    def tool_calls(self) -> list[dict]:
        return [s for s in self.steps if s["step_type"] == "tool_call"]

    @property
    def records_examined(self) -> list[str]:
        return list(self.investigation.get("records_examined") or [])

    def timeline(self) -> list[str]:
        """The investigation as a readable sequence."""
        out = [
            f"DETECTED   {self.exception['exception_id']} on "
            f"{self.exception['payment_id']}",
            f"           discrepancy {format_paise(self.exception['delta'])}"
            f"  ({self.exception['exception_type'] or 'unclassified'})",
            "INVESTIGATING",
        ]
        for step in self.steps:
            if step["step_type"] == "tool_call":
                args = ", ".join(f"{k}={v}" for k, v in (step["tool_args"] or {}).items())
                failed = "  ✗" if (step["tool_result"] or {}).get("error") else "  ✓"
                out.append(f"           {step['tool_name']}({args}){failed}")
            elif step["step_type"] == "error":
                out.append(f"           ⚠ {step['observation']}")
        out.append(f"CONCLUDED  {self.investigation['final_status']}"
                   f"  (evidence score {self.investigation['evidence_score']})")
        return out

    def why_this_score(self) -> list[str]:
        factors = self.investigation.get("score_factors") or []
        lines = [f"{'starting score':<44}{100:>6}"]
        for f in factors:
            lines.append(f"  {f['name']:<42}{f['delta']:>+6}   {f['detail']}")
        lines.append(
            f"{'evidence score':<44}{self.investigation['evidence_score']:>6}"
        )
        return lines


def reconstruct(conn: Connection, exception_id: str) -> AuditTrail | None:
    """Reassemble one investigation from the database alone."""
    exception = conn.execute(
        text(
            "SELECT exception_id, payment_id, exception_type, expected_net,"
            "       actual_net, delta, detected_by, status, evidence_score,"
            "       created_at FROM recon.exceptions WHERE exception_id = :e"
        ),
        {"e": exception_id},
    ).mappings().one_or_none()
    if exception is None:
        return None

    investigation = conn.execute(
        text(
            "SELECT * FROM recon.investigations WHERE exception_id = :e"
            " ORDER BY started_at DESC LIMIT 1"
        ),
        {"e": exception_id},
    ).mappings().one_or_none()
    if investigation is None:
        return AuditTrail(exception=dict(exception), investigation={})

    steps = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT * FROM recon.investigation_steps"
                " WHERE investigation_id = :i ORDER BY seq"
            ),
            {"i": investigation["investigation_id"]},
        ).mappings()
    ]
    evidence = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT record_type, record_id, role, amount_contribution, note"
                "  FROM recon.evidence WHERE investigation_id = :i"
                " ORDER BY role DESC, evidence_id"
            ),
            {"i": investigation["investigation_id"]},
        ).mappings()
    ]
    return AuditTrail(
        exception=dict(exception),
        investigation=dict(investigation),
        steps=steps,
        evidence=evidence,
        chain=verify_chain(conn, investigation["investigation_id"]),
    )
