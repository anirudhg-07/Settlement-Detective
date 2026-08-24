# Settlement Detective

**AI Settlement Exception Investigator** — *Don't just find the mismatch. Find out why.*

Traditional reconciliation tells you **what** doesn't match. Settlement Detective
investigates **why** — and escalates what it cannot safely explain.

Built for the Razorpay AI Buildathon, **AI Finance Controller** track.

---

## Status

| Phase | | |
|---|---|---|
| 1 | Financial model + specification | ✅ done |
| 2 | Database schema + financial primitives | ✅ done |
| 3 | Synthetic data generator | next |
| 4–16 | Exceptions → reconciliation → AI agent → evidence → UI → evaluation | pending |

## Quick start

```bash
cp .env.example .env          # then set the three passwords
docker compose up -d          # PostgreSQL 16 on :55432
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/alembic upgrade head
./.venv/bin/pytest -q         # the financial model's proof of consistency
./.venv/bin/python scripts/verify_model.py   # worked examples, every rupee traced
```

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
    guards.py              write-time rejection of impossible states
  models/                  ops / recon / gt schemas
  db/session.py            one engine per database role
alembic/versions/          0001 schemas · 0002 tables · 0003 roles + grants
tests/                     G1-G6, the financial model's proof of consistency
docs/ASSUMPTIONS.md        every assumption that changes a number
scripts/verify_model.py    the worked examples, printed
```

## Documentation

- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) — financial assumptions, including
  the fee schedule, which is **synthetic and not verified Razorpay pricing**.
