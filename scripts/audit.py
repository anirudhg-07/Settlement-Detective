"""Reconstruct an investigation from the database alone.

    python scripts/audit.py --latest
    python scripts/audit.py --exception-id EX-1DF83BF518
    python scripts/audit.py --verify-all

Everything printed here comes out of `recon`. Nothing is recomputed and no
model is called - if a number cannot be justified from the stored record, it
does not appear.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from backend.audit.trail import reconstruct, verify_chain
from backend.db.session import owner_engine
from backend.money import format_paise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect the investigation audit trail")
    p.add_argument("--exception-id")
    p.add_argument("--latest", action="store_true", help="the most recent investigation")
    p.add_argument("--verify-all", action="store_true",
                   help="check every stored trail for tampering")
    return p.parse_args()


def verify_all(conn) -> int:
    ids = [
        r[0]
        for r in conn.execute(
            text("SELECT investigation_id FROM recon.investigations ORDER BY started_at")
        )
    ]
    print(f"\nverifying {len(ids)} investigation trail(s)\n")
    broken = 0
    for investigation_id in ids:
        check = verify_chain(conn, investigation_id)
        mark = "✓" if check.intact else "✗"
        print(f"  {mark} {investigation_id}  {check.steps_checked:>2} steps  {check.detail}")
        if not check.intact:
            broken += 1
            print(f"      first bad step: seq {check.broken_at}")
    print(f"\n  {len(ids) - broken} intact, {broken} tampered\n")
    return 1 if broken else 0


def show(trail) -> None:
    e, i = trail.exception, trail.investigation
    print(f"\n{'═' * 72}")
    print(f"  AUDIT TRAIL  {e['exception_id']}")
    print(f"{'═' * 72}\n")

    print("  TIMELINE")
    for line in trail.timeline():
        print(f"    {line}")

    print("\n  TOOL CALLS")
    for step in trail.tool_calls:
        args = ", ".join(f"{k}={v}" for k, v in (step["tool_args"] or {}).items())
        print(f"    [{step['seq']:>2}] {step['tool_name']}({args})"
              f"  {step['duration_ms']}ms")

    print(f"\n  RECORDS EXAMINED  ({len(trail.records_examined)})")
    for record in trail.records_examined:
        print(f"    {record}")

    print("\n  CONCLUSION")
    print(f"    {i.get('decision', '')[:300]}")

    print("\n  EVIDENCE")
    for ev in trail.evidence:
        mark = "✓" if ev["role"] == "SUPPORTS" else "✗"
        amount = (format_paise(ev["amount_contribution"])
                  if ev["amount_contribution"] is not None else "—")
        print(f"    {mark} {ev['record_type']:<16}{ev['record_id']:<22}{amount:>12}")
        if ev["role"] != "SUPPORTS":
            print(f"      {ev['note'][:110]}")

    print("\n  WHY THIS SCORE")
    for line in trail.why_this_score():
        print(f"    {line}")

    print("\n  DISCREPANCY")
    print(f"    {'expected':<20}{format_paise(e['expected_net']):>14}")
    print(f"    {'actual':<20}{format_paise(e['actual_net']):>14}")
    print(f"    {'delta':<20}{format_paise(e['delta']):>14}")
    print(f"    {'unexplained':<20}"
          f"{format_paise(i.get('unexplained_amount') or 0):>14}")

    print("\n  INTEGRITY")
    chain = trail.chain
    print(f"    hash chain over {chain.steps_checked} steps: "
          f"{'INTACT ✓' if chain.intact else 'BROKEN ✗ at seq ' + str(chain.broken_at)}")
    print(f"    {chain.detail}")
    print(f"\n    model claimed {i.get('reasoning_confidence')}% certainty; "
          f"the decision used the computed score of {i.get('evidence_score')}.")
    print(f"{'═' * 72}\n")


def main() -> int:
    args = parse_args()
    with owner_engine().connect() as conn:
        if args.verify_all:
            return verify_all(conn)

        exception_id = args.exception_id
        if not exception_id or args.latest:
            exception_id = conn.execute(
                text(
                    "SELECT exception_id FROM recon.investigations"
                    " ORDER BY started_at DESC LIMIT 1"
                )
            ).scalar()
        if not exception_id:
            print("no investigations found — run scripts/investigate.py first")
            return 1

        trail = reconstruct(conn, exception_id)
        if trail is None:
            print(f"no exception {exception_id}")
            return 1
        show(trail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
