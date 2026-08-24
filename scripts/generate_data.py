"""Generate the FreshKart synthetic dataset.

    python scripts/generate_data.py --count 10000 --seed 42

The generated world is verified in memory *before* it is written: a clean
dataset must reconcile at 100%, and the loader refuses to persist one that does
not. Loading a dataset that already fails to reconcile would make every later
measurement meaningless.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.generation.exceptions import inject_exceptions
from backend.generation.generator import generate_world
from backend.generation.persist import persist
from backend.generation.profile import MerchantProfile
from backend.generation.verify import batch_residuals, reconcile_world
from backend.money import format_paise


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Generate the FreshKart synthetic dataset")
    p.add_argument("--count", type=int, default=10_000, help="number of payments")
    p.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible)")
    p.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=settings.as_of_date,
        help="frozen reconciliation date (YYYY-MM-DD)",
    )
    p.add_argument(
        "--exception-rate-bps",
        type=int,
        default=700,
        help="exceptions to inject, in basis points (700 = 7%%); 0 for a clean world",
    )
    p.add_argument("--dry-run", action="store_true", help="generate and verify, do not write")
    p.add_argument("--keep", action="store_true", help="append instead of truncating")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    cfg = settings.financial()
    profile = MerchantProfile()

    print(f"\nFreshKart synthetic dataset")
    print(f"  payments {args.count:,} | seed {args.seed} | as-of {args.as_of}")
    print(f"  history  {profile.history_days} days | tolerance {cfg.tolerance_paise} paise\n")

    t0 = time.perf_counter()
    world = generate_world(
        seed=args.seed, n_payments=args.count, as_of=args.as_of, cfg=cfg, profile=profile
    )
    gen_s = time.perf_counter() - t0

    s = world.stats
    print("GENERATED")
    print(f"  {'customers':<22}{s['customers']:>10,}")
    print(f"  {'orders':<22}{s['orders']:>10,}")
    print(f"  {'payments':<22}{s['payments']:>10,}")
    print(f"  {'fees':<22}{s['fees']:>10,}")
    print(f"  {'refunds':<22}{s['refunds']:>10,}")
    print(f"  {'adjustments':<22}{s['adjustments']:>10,}")
    print(f"  {'settlement batches':<22}{s['settlements']:>10,}")
    print(f"  {'settlement lines':<22}{s['settlement_items']:>10,}")
    print(f"  {'total rows':<22}{world.row_count():>10,}")
    print(f"  generated in {gen_s:.2f}s ({args.count / gen_s:,.0f} payments/s)\n")

    print("MIX")
    print(f"  method  {s['method_mix']}")
    print(f"  status  {s['status_mix']}")
    print(f"  settled {s['settled_payments']:,} | pending {s['pending_payments']:,} "
          f"| failed {s['failed_payments']:,}\n")

    print("MONEY")
    print(f"  {'gross captured':<22}{format_paise(s['gross_paise']):>18}")
    print(f"  {'fees':<22}{format_paise(-s['fee_paise']):>18}")
    print(f"  {'tax on fees':<22}{format_paise(-s['tax_paise']):>18}")
    print(f"  {'refunds':<22}{format_paise(-s['refund_paise']):>18}")
    print(f"  {'adjustments':<22}{format_paise(s['adjustment_paise']):>18}")
    print(f"  {'net settled to bank':<22}{format_paise(s['settled_net_paise']):>18}\n")

    # ---- verify the clean world before breaking any of it ---------------
    clean = reconcile_world(world, cfg, args.as_of)
    if clean["mismatches"] or batch_residuals(world):
        print("REFUSING TO CONTINUE - the clean world does not reconcile.")
        return 1
    print(f"CLEAN BASELINE  {clean['match_rate_bps'] / 100:.2f}% reconciled "
          f"({len(clean['mismatches'])} mismatches)\n")

    if args.exception_rate_bps:
        report = inject_exceptions(
            world,
            cfg=cfg,
            as_of=args.as_of,
            seed=args.seed,
            rate_bps=args.exception_rate_bps,
        )
        print(f"INJECTED  {report.total():,} exceptions "
              f"({report.total() * 10_000 // len(world.payments) / 100:.2f}% of payments)")
        for kind, n in sorted(report.injected.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:<26}{n:>6,}")
        print()

    # ---- verification before anything is written ------------------------
    t1 = time.perf_counter()
    result = reconcile_world(world, cfg, args.as_of)
    verify_s = time.perf_counter() - t1
    residuals = batch_residuals(world)

    print("VERIFICATION")
    print(f"  counts          {result['counts']}")
    print(f"  reconciled      {result['match_rate_bps'] / 100:.2f}% "
          f"(matched + legitimately pending)")
    print(f"  batch residuals {len(residuals)} (must be 0)")
    print(f"  reconciled {result['total']:,} payments in {verify_s:.2f}s "
          f"({result['total'] / verify_s:,.0f}/s)\n")

    # Every exception found must be one that was deliberately injected, and
    # every delta-visible injection must be found. Anything else means the
    # injectors are producing accounting artifacts rather than faults.
    truths = {t["payment_id"]: t for t in world.truths}
    injected_visible = {
        pid
        for pid, truth in truths.items()
        if truth["is_exception"] and (truth["injection_params"] or {}).get("delta_visible")
    }
    detected = {m.payment_id for m in result["mismatches"]}
    missed, spurious = injected_visible - detected, detected - injected_visible

    print("GROUND TRUTH")
    print(f"  exceptions injected      {sum(1 for t in truths.values() if t['is_exception']):>6,}")
    print(f"  delta-visible            {len(injected_visible):>6,}")
    print(f"  detected by arithmetic   {len(detected):>6,}")
    print(f"  missed                   {len(missed):>6,}")
    print(f"  spurious                 {len(spurious):>6,}\n")

    if residuals or missed or spurious:
        print("REFUSING TO WRITE - the dataset is not internally consistent.")
        for pid in list(missed)[:10]:
            print(f"  missed   {pid}  {truths[pid]['reason_code']}")
        for pid in list(spurious)[:10]:
            print(f"  spurious {pid}")
        for settlement_id, residual in residuals[:10]:
            print(f"  batch {settlement_id} residual {format_paise(residual)}")
        return 1

    if args.dry_run:
        print("dry run - nothing written.\n")
        return 0

    from backend.db.session import owner_engine

    t2 = time.perf_counter()
    written = persist(world, owner_engine(), truncate=not args.keep)
    load_s = time.perf_counter() - t2
    total = sum(written.values())
    print("LOADED")
    for label, n in written.items():
        print(f"  {label:<22}{n:>10,}")
    print(f"  {'total':<22}{total:>10,}")
    print(f"  loaded in {load_s:.2f}s ({total / load_s:,.0f} rows/s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
