# Settlement Detective — Phase Plan

Working checklist for the build. Six of sixteen phases done.

**Rule for every phase:** explain what and why → list files → implement → run tests
→ show results → check errors → only then move on. Never skip a phase silently.

---

## Status at a glance

| # | Phase | Status | Tests |
|---|-------|--------|-------|
| 1 | Financial model + specification | ✅ done | — |
| 2 | Database schema + financial primitives | ✅ done | 83 |
| 3 | Synthetic data generator | ✅ done | 109 |
| 4 | Exception injection | ✅ done | 130 |
| 5 | Deterministic reconciliation engine | ✅ done | 146 |
| 6 | Exception classification | ✅ done | 162 |
| 7 | AI investigation agent | ✅ done | **186** |
| 8 | Evidence builder | ⬅️ **next** | |
| 9 | Confidence / safety layer | pending | |
| 10 | Audit trail | pending | |
| 11 | Razorpay Test Mode integration | pending | |
| 12 | Backend APIs | pending | |
| 13 | Frontend dashboard | pending | |
| 14 | Evaluation | pending | |
| 15 | Stress testing | pending | |
| 16 | Demo / polish / documentation | pending | |

## Where the numbers stand

| Metric | Value |
|---|---|
| Payments in dataset | 10,055 |
| Exceptions injected | 700 (6.96%) |
| Reconciled (matched + legitimately pending) | 94.35% |
| Detection precision | **100.00%** — no healthy payment ever flagged |
| Detection recall | **100.00%** (700/700) |
| Classification accuracy, where a single correct type exists | **100.00%** (633/633) |
| Classification accuracy, over all injected | 90.43% |
| Throughput | ~24,000 payments/s |
| Baseline's remaining gap | 67 `MULTI_CAUSE` cases |

---

## Commands

```bash
docker compose up -d                                        # Postgres on :55432
./.venv/bin/alembic upgrade head                            # migrations
./.venv/bin/pytest -q                                       # 162 tests
./.venv/bin/python scripts/verify_model.py                  # worked examples
./.venv/bin/python scripts/generate_data.py --count 10000   # generate + load
./.venv/bin/python scripts/reconcile.py                     # reconcile + classify + score
```

---

# ✅ Phase 1 — Financial model

**Goal:** get the money right on paper before writing a line of code.

Delivered: entity relationships, sign convention, the reconciliation formula,
the exception taxonomy, a worked ₹1,000 example, and the test list.

**Decisions that shaped everything after:**
- Settlement is a **batch** with **line items**, not one row per payment. Four
  of the ten exception types are unrepresentable without it.
- Fee and GST are **not** reversed on refund (`REVERSE_FEE_ON_REFUND=false`).
  A fully refunded ₹1,000 payment nets the merchant **−₹23.60**.
- All money is **integer paise**. `float` is rejected at every boundary.

---

# ✅ Phase 2 — Schema + financial primitives

**Files:** `backend/{money,config,enums}.py`,
`backend/reconciliation/{fees,timing,settlement_math,guards}.py`,
`backend/models/{base,ops,recon,gt}.py`, migrations `0001`–`0003`.

**Safety controls enforced by Postgres, not by convention:**

| Control | How |
|---|---|
| Agent cannot read ground truth | `sd_agent` has **no grant** on schema `gt` |
| Agent cannot modify money | `SELECT` only on `ops` |
| Audit trail append-only | `INSERT`, no `UPDATE`/`DELETE` on `investigation_steps` |
| No floats in money code | AST scan over 10 modules |
| No wall-clock reads | AST scan for `datetime.now` / `date.today` |

**Caught while building:** a `FAILED` payment carrying a stray fee row produced
a negative expectation — the merchant "owing" money on a payment that never
captured. Fixed: non-settleable statuses net exactly zero.

---

# ✅ Phase 3 — Synthetic data generator

**Files:** `backend/generation/{profile,ids,generator,verify,persist}.py`,
`scripts/generate_data.py`.

FreshKart profile: UPI-dominant method mix, grocery basket sizes, 4% payment
failures, 9% refunds, 1.2% adjustments, 90 days of history. Every rate in basis
points so no probability is a float. Same seed → identical dataset.

**The generator verifies itself before writing.** A clean world must reconcile
at 100%; the loader refuses to persist one that does not.

**Bug it found in the reconciler:** a refund settles on its own T+2 cycle. The
reconciler only counted it after `eligibility + grace`, but the money had
already left at `eligibility` — a phantom discrepancy on healthy data. The
cutoff now uses the eligibility date. Grace answers a different question
("is this late enough to call missing?").

---

# ✅ Phase 4 — Exception injection

**Files:** `backend/generation/exceptions.py`, migration `0005`.

13 injectors: the 10 taxonomy types plus three hard families.

| Family | Why a rule struggles |
|---|---|
| `MULTI_CAUSE` | Two faults in one delta; single-hypothesis matching finds no exact match |
| `CROSS_ENTITY` | Explanation is an adjustment with `payment_id = NULL`, reachable only via the batch |
| `TIMING_SHIFTED` | Looks identical to a missing refund until you check batch status |

**Not every exception has a delta.** `DUPLICATE_PAYMENT`, `SETTLEMENT_TIMING`
and `UNEXPECTED_ADJUSTMENT` reconcile perfectly — 132 of the 700.

**`UNKNOWN_DISCREPANCY` has no explanation on purpose.** If ground truth held an
answer, an honest "I don't know" would score as a failure — training the whole
system to guess.

**Bug it found:** a fully-refunded **UPI** payment expects exactly ₹0. With its
credit line deleted, the refund debit still took −₹2,900 — and the reconciler
called it `MATCHED` because "nothing was owed". It was matching on the
*expectation* instead of the *delta*. Caught by the loader's pre-write check
(567 found vs 568 expected), which is exactly why that check exists.

---

# ✅ Phase 5 — Reconciliation engine

**Files:** `backend/reconciliation/engine.py`, `scripts/reconcile.py`,
migration `0006`.

Sweeps the database in one bulk pass, writes `recon_runs` / `recon_results` /
`exceptions`. **Contains no arithmetic of its own** — every number comes from
the Phase 2 pure functions, which is what makes the G4 property tests proofs
about the engine rather than about a helper.

Batch identity checked separately with **zero tolerance**: a payout *is* the sum
of its lines.

---

# ✅ Phase 6 — Exception classification

**Files:** `backend/reconciliation/classifier.py`.

Builds every cause the records support and finds the one that accounts for the
delta **exactly**. Zero matches, or two that each claim the whole delta →
`UNKNOWN_DISCREPANCY`, not a guess. Plus three rules for the exceptions with no
delta at all.

**Stated blind spot, pinned by a test:** single-hypothesis only. When two faults
share one discrepancy the baseline declines to answer. That is the gap the agent
exists to close.

> ⚠️ **Read the 90.43% as an upper bound.** These rules were written knowing
> exactly which faults the injectors create. Phase 15 must throw unseen variants
> at them or the number is flattering itself.

---

# ✅ Phase 7 — AI investigation agent

**Goal:** investigate the exceptions the baseline cannot close, using tools —
never by being handed the database.

**Built:** `backend/agents/{llm,tools,prompts,investigator}.py`,
`scripts/investigate.py`, 24 tests (almost all against a scripted fake model,
so the safety rules are exercised without spending quota).

**Three infrastructure bugs found and fixed:**
- macOS framework Python has an empty CA store — every HTTPS call failed with a
  certificate error pointing nowhere near the cause. Fixed with `httpx`.
- The network advertises IPv6 without a working route to Google, so requests
  wedged in `SYN_SENT` and hung with no error. Fixed by forcing IPv4.
- `gemini-2.5-flash-lite` is retired for new keys; the live model is
  `gemini-3.5-flash-lite`.

**The safety hole the first live run exposed:** the model cited *the payment
under investigation* as evidence for its own discrepancy. The cited amount
equalled the whole delta, so the residual went to zero and produced a confident
`RESOLVED` — a false-resolution generator. Now blocked: a record cannot explain
itself (except `MISSING_SETTLEMENT`, where the database verifies the absence),
`unresolved=true` keeps the full residual, and "resolved as unknown" is refused
as a contradiction.

**Needs:** `LLM_API_KEY` in `.env`

**Build:**
- `backend/agents/tools.py` — the controlled tool surface:
  `get_payment`, `get_order`, `get_refunds`, `get_fee`, `get_adjustments`,
  `get_settlement`, `get_settlement_items`, `calculate_expected_settlement`,
  `find_related_transactions`, `search_batch_adjustments`
- `backend/agents/investigator.py` — the tool-calling loop
- `backend/agents/prompts.py` — versioned prompts (`prompt_version` is stored)

**Hard rules:**
- Every tool connects as **`sd_agent`** — no access to `gt`, read-only on `ops`
- **All arithmetic is done by tools**, never by the model
- Validate every tool argument before execution; no arbitrary SQL
- Cap tool calls per investigation; stop when evidence is sufficient
- Log every call to `investigation_steps` (append-only)
- On LLM/API failure → `UNRESOLVED` + escalate, never a guess

**Success looks like:** decomposing `MULTI_CAUSE` deltas the baseline declines,
and correctly refusing the 43 genuine unknowns.

---

# Phase 8 — Evidence builder

Every resolved exception gets an evidence package: the records cited, each with
a signed `amount_contribution`.

**The rule that makes "I don't know" computable:**

```
unexplained_amount = delta − Σ evidence.amount_contribution
RESOLVED requires unexplained_amount == 0 (within tolerance)
```

Evidence rows must cite real record IDs — validated against `ops` before saving.

---

# Phase 9 — Confidence / safety layer

Two numbers, deliberately separate:
- `reasoning_confidence` — what the model says. **Recorded, never trusted.**
- `evidence_score` — computed by code from: records found, arithmetic fully
  reconciling, no conflicting records, unexplained residual, known pattern.

| Score | Outcome |
|---|---|
| ≥ 90 | `RESOLVED` |
| 60–89 | `REVIEW` |
| < 60 | `ESCALATED` |

Thresholds live in `FinancialConfig` and are already wired.

---

# Phase 10 — Audit trail

Mostly built already: `investigations` + `investigation_steps` exist and are
append-only by grant. Remaining work is completeness — every tool call, argument,
result, and timing recorded, and a query that reconstructs an investigation
end to end for the UI.

---

# Phase 11 — Razorpay Test Mode

**Needs:** `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (Test Mode only).

**Non-negotiable:** check the current official docs first. Never invent an
endpoint, parameter, or response field. If test mode does not provide enough
data, say so plainly and keep the synthetic dataset as the evaluation
environment.

Also verify the fee schedule (2% / 1.9% / 0%) and the T+2 cycle — currently
**synthetic defaults, not asserted Razorpay pricing**.

All secret-key calls stay server-side.

---

# Phase 12 — Backend APIs

FastAPI over the existing modules. Roughly:

```
GET  /api/runs/latest          Command Centre totals
GET  /api/exceptions           filter by status / type / value / confidence
GET  /api/exceptions/{id}      detail + evidence + timeline
POST /api/exceptions/{id}/investigate
GET  /api/metrics              the evaluation numbers
```

Read-only for financial records. The frontend never gets a secret.

---

# Phase 13 — Frontend dashboard

Next.js + TypeScript. Clean fintech, not overdesigned. Five screens:

1. **Command Centre** — totals, match rate, resolution rate, hours saved
2. **Exceptions** — the table + filters
3. **Investigation** — *the most important screen*: the timeline and the real
   tool calls, expandable
4. **Evidence** — every record and the exact calculation
5. **Analytics** — precision, recall, accuracy, false-resolution rate, throughput

---

# Phase 14 — Evaluation

Nine metrics from the spec, over 10,000 records, **baseline vs AI on identical
exceptions** (both modes already write to the same `investigations` table).

**The headline is False Resolution Rate, not accuracy.** 95% resolved with 4%
false resolutions is worse than 70% resolved with 0.2%.

State the manual-investigation-time assumption explicitly when quoting hours
saved.

---

# Phase 15 — Stress testing

The 12 scenarios from the spec, plus the one that matters most:

> **Unseen variants.** Fault shapes neither the rules nor the prompts were built
> against. Without this, Phase 6's 90.43% is measuring memorisation.

Also: LLM/API failure, conflicting records, missing related records — every one
must degrade to **escalate**, never to a guess.

---

# Phase 16 — Demo, polish, documentation

Three cases, in this order:
1. **Easy** — refund + fee fully explain the discrepancy
2. **Multi-step** — the agent hops across records to find the cause
3. **Unknown** — records checked, nothing explains it, **escalate**

Case 3 is the one that wins trust. It shows the system knows when it doesn't
know.

---

## Open risks

| Risk | Mitigation |
|---|---|
| Baseline rules are overfitted to known injectors | Phase 15 unseen variants |
| AI's quantitative headroom is only ~67 cases | Also measure evidence quality + escalation safety |
| Fee schedule / T+2 cycle unverified | Phase 11 checks official docs |
| Razorpay test mode may not expose enough settlement data | Synthetic stays the evaluation environment; say so plainly |
| `MULTI_CAUSE` decomposition may be hard for the agent too | It may be. Report it honestly either way. |

## Things not to build

Generic chatbot · multi-agent swarm · voice · WhatsApp · mobile app · real bank
integration · microservices · Kubernetes · ML models that aren't earning their
place · autonomous financial transactions.
