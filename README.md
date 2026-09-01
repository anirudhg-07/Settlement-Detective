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
| 6 | Exception classification | ✅ done |
| 7 | AI investigation agent | ✅ done |
| 8 | Evidence builder | ✅ done |
| 9 | Confidence / safety layer | ✅ done |
| 10 | Audit trail | ✅ done |
| 11 | Razorpay Test Mode | ✅ done |
| 12 | Backend APIs | next |
| 13–16 | UI → evaluation → stress → demo | pending |

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

## Baseline (Phases 5–6, 10,055 payments)

| | |
|---|---|
| Reconciled (matched + legitimately pending) | 94.35% |
| Detection precision | **100.00%** — no healthy payment ever flagged |
| Detection recall | **100.00%** (700/700) |
| Classification, where a single correct type exists | **100.00%** (633/633) |
| Classification, over all injected exceptions | 90.43% |
| Throughput | ~24,000 payments/s |

**The baseline is deliberately strong.** It is what the AI must beat, and a
weak one would make Phase 14's comparison worthless.

Its remaining gap is one thing: **67 `MULTI_CAUSE` exceptions** where two faults
share a single discrepancy. No individual cause matches the delta, so the
baseline declines to answer rather than guess. That is correct behaviour — and
it is exactly the gap an investigating agent exists to close.

> **Caveat worth stating.** These rules were written knowing which faults the
> injectors create. Real exception queues are not so obliging, and Phase 15's
> stress tests must include variants the rules have never seen. The 90.43%
> should be read as an upper bound on what rules achieve, not a typical one.

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
  agents/
    llm.py                 provider adapter: cached, throttled, IPv4-forced
    tools.py               the controlled tool surface (sd_agent only)
    prompts.py             versioned system prompt
    investigator.py        the loop + the false-resolution guards
    evidence.py            what each record can actually support
    scoring.py             the evidence score — code decides, not the model
  audit/trail.py           hash-chained trail + reconstruction
  razorpay/                Test Mode client + mapping (verified endpoints only)
  models/                  ops / recon / gt schemas
  db/session.py            one engine per database role
alembic/versions/          0001 schemas · 0002 tables · 0003 roles + grants
tests/                     G1-G6, the financial model's proof of consistency
docs/ASSUMPTIONS.md        every assumption that changes a number
scripts/verify_model.py    the worked examples, printed
scripts/generate_data.py   generate, verify, then load the dataset
scripts/reconcile.py       run the engine, score it against ground truth
scripts/investigate.py     run the AI agent (checkpointed, cached, budgeted)
scripts/audit.py           reconstruct an investigation; verify trail integrity
scripts/razorpay_sync.py   read Razorpay Test Mode; cross-check our arithmetic
```

## Documentation

- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) — financial assumptions, including
  the fee schedule, which is **synthetic and not verified Razorpay pricing**.
