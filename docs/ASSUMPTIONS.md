# Financial Model Assumptions

Every assumption that changes a number, and how to change it back.

## Decisions confirmed in Phase 1

| # | Decision | Value | Where it lives |
|---|----------|-------|----------------|
| 1 | Settlement is a **batch** (`ops.settlements`) with **line items** (`ops.settlement_items`), not one row per payment | — | `backend/models/ops.py` |
| 2 | Processing fee and its GST are **not** reversed when a payment is refunded | `reverse_fee_on_refund = False` | `backend/config.py` |
| 3 | All money is **integer paise**; `float` is rejected at every boundary | — | `backend/money.py` |

Decision 2 is the one that most affects the numbers: a fully refunded ₹1,000
payment nets the merchant **−₹23.60**, not ₹0. Setting
`REVERSE_FEE_ON_REFUND=true` flips it pro-rata, and
`tests/test_g3_expected_settlement.py` asserts both behaviours.

## Rounding

`ROUND_HALF_UP` at the whole-paise level, applied in exactly one function
(`backend.money.round_half_up`) and reached only through `apply_bps`. Half-up
rather than banker's rounding, because that is the invoicing convention and
agreeing with the counterparty matters more here than statistical neutrality.

## Tolerance

`TOLERANCE_PAISE = 1` (₹0.01), applied per payment. Batch-level reconciliation
uses **no tolerance at all** — a batch is an arithmetic identity, not a
comparison between two sources. `tests/test_g5_tolerance_timing.py` asserts a
clean transaction still matches at zero tolerance, so the tolerance is not
concealing systematic drift.

## Settlement timing

`T + 2` business days to become eligible, plus `1` business day of grace before
an unsettled payment is called `MISSING_SETTLEMENT`.

**Business days exclude Saturday and Sunday only.** No public-holiday calendar
is modelled. A holiday calendar would shift eligibility dates but change no
amount, and the synthetic generator is holiday-free by construction.

All timing decisions read `as_of_date`, never the wall clock — asserted
statically by `test_reconciliation_never_reads_the_wall_clock`.

## Fee schedule — NOT verified Razorpay pricing

| Method | Rate |
|--------|------|
| Card | 2.00% |
| Netbanking | 1.90% |
| UPI | 0.00% |
| Wallet | 2.00% |
| GST on fee | 18% |

> These are **synthetic model defaults**. They have not been checked against
> official Razorpay documentation and are not presented as Razorpay's pricing.
> Phase 11 verifies them against the live docs and either aligns the schedule or
> labels it explicitly as a synthetic pricing model. The engine reads them from
> config, so a correction is a `.env` change with no code impact.

## Currency

INR only in v1, enforced by a database `CHECK` constraint and a write-time
guard. A foreign-currency row fails loudly rather than being reconciled as if
its minor units were paise.

## Ground truth

`gt.case_truth` is readable by the `sd_eval` role and by nobody else. The
`sd_agent` role — which the AI investigation tool layer connects as — holds no
grant on the schema, so a leak surfaces as `permission denied` rather than
depending on nobody writing a careless join. Asserted by
`test_agent_role_cannot_read_ground_truth`.
