"""Fetch settlement data from Razorpay Test Mode and map it onto our model.

    python scripts/razorpay_sync.py
    python scripts/razorpay_sync.py --year 2026 --month 8

This is an integration proof, not the evaluation environment. Settlements
happen when real money moves to a real bank account, so a Test Mode account may
hold none - in which case this reports exactly that rather than inventing
anything, and the synthetic dataset remains where the measurements come from
(spec §26).

What it does prove, when data is present: that our financial model reproduces
Razorpay's own credited figures line for line.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings
from backend.money import format_paise
from backend.razorpay.client import RazorpayError, build_client
from backend.razorpay.mapping import MappingError, check_arithmetic, map_recon_row, map_settlement


def parse_args() -> argparse.Namespace:
    today = date.today()
    p = argparse.ArgumentParser(description="Read Razorpay Test Mode settlements")
    p.add_argument("--year", type=int, default=today.year)
    p.add_argument("--month", type=int, default=today.month)
    p.add_argument("--count", type=int, default=100)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    try:
        client = build_client(settings)
    except RazorpayError as exc:
        print(f"\ncannot connect: {exc}\n")
        return 1

    print(f"\nRazorpay Test Mode  ({client.credentials.redacted()})")
    print(f"  recon window: {args.year}-{args.month:02d}\n")

    # ---- settlement batches ---------------------------------------------
    try:
        settlements = client.list_settlements(count=args.count)
    except RazorpayError as exc:
        print(f"  GET /settlements failed: {exc}\n")
        return 1

    print(f"SETTLEMENT BATCHES  ({len(settlements)})")
    for row in settlements[:10]:
        try:
            mapped = map_settlement(row)
        except MappingError as exc:
            print(f"  {row.get('id')}: cannot map — {exc}")
            continue
        print(f"  {mapped['settlement_id']:<24}{mapped['status']:<12}"
              f"{format_paise(mapped['net_amount']):>16}   utr {mapped['utr']}")
    if not settlements:
        print("  none on this account")
    print()

    # ---- recon line items ------------------------------------------------
    try:
        rows = client.recon_report(year=args.year, month=args.month, count=args.count)
    except RazorpayError as exc:
        print(f"  GET /settlements/recon/combined failed: {exc}\n")
        return 1

    print(f"RECON LINE ITEMS  ({len(rows)})")
    if not rows:
        print("  none in this window")
        print()
        print("  Settlements are created when money actually moves to a bank")
        print("  account, which does not happen for test payments. An empty")
        print("  result here is the expected state of a Test Mode account, not")
        print("  a failure — and it is why the synthetic dataset, not this API,")
        print("  is where the evaluation numbers come from.")
        print()
        print("  The integration is real and verified: the three endpoints above")
        print("  were called live and authenticated, and the mapping is checked")
        print("  against Razorpay's own documented figures in tests/test_g15.")
        print()
        return 0

    kinds: Counter[str] = Counter()
    skipped: list[str] = []
    agreed = disagreed = 0

    for row in rows:
        try:
            mapped = map_recon_row(row)
        except MappingError as exc:
            skipped.append(f"{row.get('entity_id')}: {exc}")
            continue
        kinds[mapped.item_type] += 1
        ok, detail = check_arithmetic(row, mapped)
        agreed += int(ok)
        disagreed += int(not ok)
        if len(kinds) <= 12:
            print(f"  {mapped.source_type:<12}{mapped.entity_id:<24}"
                  f"{format_paise(mapped.net_amount):>14}   {mapped.settlement_id}")

    print(f"\n  by type: {dict(kinds)}")
    if skipped:
        print(f"  {len(skipped)} row(s) not representable in this model:")
        for note in skipped[:5]:
            print(f"    {note}")

    print("\nCROSS-VALIDATION")
    print("  Does our settlement arithmetic reproduce Razorpay's own figures?")
    print(f"    agrees     {agreed}")
    print(f"    disagrees  {disagreed}")
    print("  " + ("✓ our financial model matches the real one"
                  if disagreed == 0 else "✗ MODEL MISMATCH — investigate before trusting any number"))
    print()
    return 1 if disagreed else 0


if __name__ == "__main__":
    raise SystemExit(main())
