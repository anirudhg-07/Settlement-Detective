"""Run the deterministic reconciliation engine over the loaded dataset.

    python scripts/reconcile.py

Also prints how the run scored against ground truth. That measurement uses the
`sd_eval` role, which is the only role permitted to read schema `gt` - the
engine itself connects as the owner and never sees it.

The recall figure here is deliberately reported two ways. Arithmetic can only
find exceptions that move money; a duplicate charge or a late-but-correct
settlement reconciles perfectly and is invisible to it. Quoting only the
flattering number would misrepresent what a reconciler can do, and would leave
nothing for Phase 6 to actually improve.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from backend.config import get_settings
from backend.db.session import eval_engine, owner_engine
from backend.money import format_paise
from backend.reconciliation.engine import run_reconciliation


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Reconcile the loaded dataset")
    p.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of_date)
    p.add_argument("--no-score", action="store_true", help="skip the ground-truth score")
    return p.parse_args()


def score_against_truth(run_id: str) -> None:
    """Compare what the engine found against what was actually injected."""
    with eval_engine().connect() as conn:
        truth = {
            r.payment_id: (r.reason_code, bool(r.delta_visible))
            for r in conn.execute(
                text(
                    "SELECT payment_id, reason_code,"
                    "       COALESCE((injection_params->>'delta_visible')::bool, false)"
                    "         AS delta_visible"
                    "  FROM gt.case_truth WHERE is_exception"
                )
            )
        }
        detected = {
            r.payment_id
            for r in conn.execute(
                text("SELECT payment_id FROM recon.exceptions WHERE run_id = :r"),
                {"r": run_id},
            )
        }

    injected = set(truth)
    visible = {pid for pid, (_, vis) in truth.items() if vis}
    invisible = injected - visible

    true_positives = detected & injected
    false_positives = detected - injected
    missed_visible = visible - detected

    def pct(num: int, den: int) -> str:
        return f"{num * 100 / den:.2f}%" if den else "n/a"

    print("SCORED AGAINST GROUND TRUTH")
    print(f"  {'exceptions injected':<34}{len(injected):>8,}")
    print(f"    {'move money (findable by arithmetic)':<32}{len(visible):>8,}")
    print(f"    {'no delta (need rules - Phase 6)':<32}{len(invisible):>8,}")
    print()
    print(f"  {'detected by the engine':<34}{len(detected):>8,}")
    print(f"  {'true positives':<34}{len(true_positives):>8,}")
    print(f"  {'false positives':<34}{len(false_positives):>8,}")
    print()
    print(f"  {'precision':<34}{pct(len(true_positives), len(detected)):>8}")
    print(f"  {'recall (delta-visible only)':<34}{pct(len(true_positives), len(visible)):>8}")
    print(f"  {'recall (all injected)':<34}{pct(len(true_positives), len(injected)):>8}")
    print()

    if missed_visible:
        print(f"  MISSED {len(missed_visible)} exceptions that move money:")
        for pid in list(missed_visible)[:10]:
            print(f"    {pid}  {truth[pid][0]}")
        print()
    if false_positives:
        print(f"  {len(false_positives)} FALSE POSITIVES:")
        for pid in list(false_positives)[:10]:
            print(f"    {pid}")
        print()

    by_reason: dict[str, list[int]] = {}
    for pid, (reason, _) in truth.items():
        found, total = by_reason.setdefault(reason, [0, 0])
        by_reason[reason] = [found + (pid in detected), total + 1]
    print("  detection by injected cause")
    for reason, (found, total) in sorted(by_reason.items(), key=lambda kv: -kv[1][1]):
        bar = "#" * (found * 20 // total) if total else ""
        print(f"    {reason:<26}{found:>4}/{total:<5}{pct(found, total):>8}  {bar}")
    print()


def main() -> int:
    args = parse_args()
    cfg = get_settings().financial()

    print(f"\nReconciling as of {args.as_of} | tolerance {cfg.tolerance_paise} paise\n")
    summary = run_reconciliation(owner_engine(), cfg=cfg, as_of=args.as_of)

    print(f"RUN {summary.run_id}")
    print(f"  {'payments processed':<34}{summary.records_processed:>8,}")
    for status, count in sorted(summary.counts.items(), key=lambda kv: -kv[1]):
        print(f"    {status:<32}{count:>8,}")
    print(f"  {'reconciled (matched + pending)':<34}{summary.match_rate_bps / 100:>7.2f}%")
    print(f"  {'exceptions opened':<34}{summary.exceptions_written:>8,}")
    print()
    print(f"  {'settlement batches checked':<34}{summary.batches_checked:>8,}")
    print(f"  {'batches out of balance':<34}{len(summary.batches_out_of_balance):>8,}")
    for settlement_id, residual in summary.batches_out_of_balance[:5]:
        print(f"    {settlement_id}  residual {format_paise(residual)}")
    print()
    print(f"  {'load':<34}{summary.load_seconds:>7.2f}s")
    print(f"  {'compute':<34}{summary.compute_seconds:>7.2f}s")
    print(f"  {'write':<34}{summary.write_seconds:>7.2f}s")
    print(f"  {'throughput':<34}{summary.throughput:>7,.0f} payments/s")
    print()

    if not args.no_score:
        score_against_truth(summary.run_id)

    return 1 if summary.batches_out_of_balance else 0


if __name__ == "__main__":
    raise SystemExit(main())
