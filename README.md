# Settlement Detective

**AI Settlement Exception Investigator** — *Don't just find the mismatch. Find out why.*

Traditional reconciliation tells you **what** doesn't match. Settlement Detective
investigates **why** — and escalates what it cannot safely explain.

---

## Status

| Phase | | |
|---|---|---|
| 1 | Financial model + specification | ✅ done |
| 2 | Database schema + financial primitives | ✅ done |
| 3 | Synthetic data generator | ✅ done |
| 4 | Exception injection | ✅ done |
| 5 | Deterministic reconciliation engine | ✅ done |
| 6 | Exception classification | next |
| 7–16 | AI agent → evidence → confidence → UI → evaluation | pending |

## Quick start

```bash
cp .env.example .env          # then set the three passwords
docker compose up -d          # PostgreSQL 16 on :55432
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/alembic upgrade head
./.venv/bin/pytest -q         # the financial model's proof of consistency
./.venv/bin/python scripts/verify_model.py    # worked examples, every rupee traced
./.venv/bin/python scripts/generate_data.py --count 10000   # the FreshKart dataset
./.venv/bin/python scripts/reconcile.py       # reconcile + score vs ground truth
```

## Baseline (Phase 5, 10,055 payments)

| | |
|---|---|
| Reconciled (matched + legitimately pending) | 94.35% |
| Detection precision | **100.00%** — no healthy payment ever flagged |
| Recall, exceptions that move money | **100.00%** (568/568) |
| Recall, all injected exceptions | 81.14% (568/700) |
| Throughput | ~24,000 payments/s |

The gap between the two recall figures is the honest one: 132 injected
exceptions — duplicate charges, late-but-correct settlements, unauthorised
adjustments — reconcile perfectly. Arithmetic cannot see them. That is what
Phase 6's rules exist to close.

## Design commitments

**Code does the arithmetic; the LLM never does.** Every rupee is an integer
count of paise, and `float` is rejected at every boundary of the money layer —
enforced by a static check over the financial modules, not by convention.

**The agent cannot see the answers.** Ground truth lives in schema `gt`, and the
role the AI tool layer connects as (`sd_agent`) holds no grant on it. A leak is
a `permission denied` at runtime, not a code-review miss.

**The audit trail cannot be rewritten.** `sd_agent` has `INSERT` and no
`UPDATE`/`DELETE` on `recon.investigation_steps`.

**"I don't know" is computed, not stylistic.** Resolution requires the evidence
to account for the discrepancy exactly; whatever is left over is reported as
`unexplained_amount` and escalated.

## Layout

```
backend/
  money.py                 integer-paise primitives; the only rounding site
  config.py                FinancialConfig - every number that changes an outcome
  enums.py                 controlled vocabularies
  reconciliation/
    fees.py                fee and GST
    timing.py              settlement eligibility, business days
    settlement_math.py     expected settlement, sign convention, the decision
    engine.py              the database sweep; owns no arithmetic
    guards.py              write-time rejection of impossible states
  generation/
    profile.py             FreshKart's distributions - basket sizes, method mix
    generator.py           the world builder
    exceptions.py          13 injectors + ground truth
    verify.py              in-memory reconciliation: a clean world must be 100%
    persist.py             dependency-ordered bulk load
  models/                  ops / recon / gt schemas
  db/session.py            one engine per database role
alembic/versions/          0001 schemas · 0002 tables · 0003 roles + grants
tests/                     G1-G6, the financial model's proof of consistency
docs/ASSUMPTIONS.md        every assumption that changes a number
scripts/verify_model.py    the worked examples, printed
scripts/generate_data.py   generate, verify, then load the dataset
scripts/reconcile.py       run the engine, score it against ground truth
```

## Documentation

- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) — financial assumptions, including
  the fee schedule, which is **synthetic and not verified Razorpay pricing**.
