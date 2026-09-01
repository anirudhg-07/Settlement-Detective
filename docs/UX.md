# Phase 13 — UI/UX design

## Who this is for

A finance operations analyst at FreshKart. Monday morning. Last night's run
produced 700 exceptions against 10,055 payments. They have about two hours.

They are not a data explorer and not an engineer. They will be asked, later, to
justify why a case was closed.

## The job has changed

Traditional reconciliation software helps someone **find** problems.
Settlement Detective has already found them and formed conclusions. So the
analyst's real task is different:

> **Do I believe this?** — yes, no, or not sure. In seconds. Hundreds of times.

Every screen serves that question. Two failure modes to design against:

| Failure | Cause | Cost |
|---|---|---|
| Rubber-stamping | doubting is expensive | false resolutions ship |
| Re-checking everything by hand | evidence isn't legible | no time saved; product is pointless |

**Design goal: calibrated trust.** Make doubt cheap, so trust is earned per
case rather than granted wholesale.

---

## The core loop

```
     ┌────────────────────────────────────────────┐
     │  1. Triage      what needs me today?       │
     │  2. Scan        which of these first?      │
     │  3. Judge       do I believe this one?     │  ← the product
     │  4. Act         accept / reject / escalate │
     │  5. Return      next case, context kept    │
     └────────────────────────────────────────────┘
```

Steps 1, 2, 4 and 5 must be nearly frictionless so that all the attention is
spent on step 3.

**Return-to-queue is a first-class requirement.** An analyst working 40 cases
must never lose their filters, scroll position, or place in the list. Deep-link
every case (`/exceptions/EX-1DF83BF518`) so a case can be shared with a
colleague or pasted into a ticket.

### Case Status Lifecycle

The system follows a strict state machine:

```
DETECTED
    ↓
INVESTIGATING
    ↓
┌───────────────┬───────────────┐
↓               ↓               ↓
RESOLVED      REJECTED       ESCALATED
```

**AI/System decision:**
- `RESOLVED` (System recommendation)

**Human analyst decision:**
- `ACCEPTED` / `REJECTED` / `ESCALATED`

**AI resolution ≠ human approval.**
The AI may recommend that a case is resolved, but the analyst remains responsible for accepting, rejecting, or escalating the recommendation. What each state means and allowed UI actions must be clearly defined in the UI.

---

## Screen 1 — Command Center

**Question it answers:** *what needs me today?*

Not a metrics wall. Match rate is context; the queue split is the headline.

```
┌──────────────────────────────────────────────────────────────────────┐
│  FreshKart · settlement run 2026-01-31            chain intact ✓      │
│                                                    batches balanced ✓ │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   NEEDS ATTENTION                              NO ACTION             │
│   ┌──────────────┬──────────────┐              9,149  matched        │
│   │   REVIEW     │  ESCALATED   │                338  in settlement  │
│   │      0       │      2       │                     window         │
│   │    ₹0.00     │  ₹15,901.28  │                                    │
│   └──────────────┴──────────────┘              ─────────────────────  │
│                                                 94.35% reconciled     │
│   AUTO-RESOLVED — spot-check                                          │
│   3 cases · ₹27,779.02 accounted for                                  │
│                                     [ review a random sample → ]      │
└──────────────────────────────────────────────────────────────────────┘
```

Design decisions:

- **Command Center Priority:** "What needs me today?" Money requiring human attention visually dominates match-rate statistics. Match rate remains useful context but should not dominate the screen.
- **Counts carry money.** Finance people think in rupees. "2 escalated" means
  nothing; "2 escalated, ₹15,901 at risk" is a priority.
- **Auto-resolved is not hidden.** It gets a spot-check affordance, because an
  analyst who never samples the resolved pile has no basis for trusting it.
- **Run integrity is in the header, always.** If the hash chain ever breaks or
  a batch stops balancing, that is not a detail buried in a settings page.
- **Zero escalations is a success state**, rendered as one — not as an empty
  table with a shrug.

---

## Screen 2 — Exception Queue

**Question it answers:** *which of these first?*

```
┌──────────────────────────────────────────────────────────────────────┐
│ [Needs you] [Auto-resolved] [All]     cause ▾  value ▾  confidence ▾ │
├──────────────────────────────────────────────────────────────────────┤
│ CASE           CAUSE                    AT RISK   CONF   WHY         │
│ EX-57C3C105    Missing settlement    −₹13,921.46   100   captured 5  │
│                                                          Dec, never  │
│                                                          paid out    │
│ EX-BD68AD8E    Missing refund        +₹13,571.00   100   refund due, │
│                                                          never debit │
│ EX-074D82DE    Duplicate payment           ₹0.00     0   ⚠ agent     │
│                                                          couldn't    │
│                                                          conclude    │
└──────────────────────────────────────────────────────────────────────┘
```

Design decisions:

- **Default sort is money at risk, not case ID.** Nobody triages alphabetically.
- **Every row carries a one-line "why".** Scanning ten rows should not require
  ten clicks. This comes from `investigation.decision`, truncated.
- **A confidence of 0 reads as a flag, not a blank.** It means *the system
  declined* — which is information, not absence.
- **Filters mirror how triage actually happens:** by cause (I know fee
  mismatches), by value (anything over ₹10,000), by confidence (show me what
  the system was unsure about).

### Bulk accept — the dangerous feature, designed carefully

An analyst facing 400 auto-resolved cases will want to accept them at once.
That is a legitimate need and also exactly how false resolutions get shipped.

The compromise:

- Bulk accept is available **only** for the `RESOLVED` band.
- Before confirming, the UI **forces a sample**: *"You're accepting 412 cases.
  Review 5 at random first?"* with the five already loaded.
- The confirmation states what is being accepted in money, not just count.
- Every bulk acceptance is recorded with the sample that was reviewed.

This keeps the speed while making blind acceptance a deliberate act rather
than a default.

---

## Screen 3 — Investigation View · **this is the product**

**Question it answers:** *do I believe this?*

Everything else exists to get the analyst here and back. Structure it as an
argument, in the order a sceptic reads one: claim → arithmetic → evidence →
how the system rated itself → what it actually did.

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Previous Case  EX-BD68AD8E69 · pay_zehoEcfhEG7l9d      Next Case → │
│                  3 / 40                                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  MISSING REFUND                        EVIDENCE SUFFICIENCY 100/100  │
│  A ₹13,571.00 refund was processed on 19 Nov and is past its         │
│  settlement date, but was never debited from any payout.             │
│  FreshKart is still holding money it has already returned.           │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  DOES IT ADD UP?                                                     │
│                                                                      │
│    expected                              −₹320.28                    │
│    actually settled                   ₹13,250.72                     │
│    ────────────────────────────────────────────                      │
│    discrepancy                        ₹13,571.00                     │
│      refund rfnd_rGtIHZjQ…  never debited   −₹13,571.00  ✓ verified  │
│    ────────────────────────────────────────────                      │
│    unexplained                             ₹0.00   ✓                 │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  EVIDENCE                                                            │
│    ✓ refund   rfnd_rGtIHZjQ105jSm     ₹13,571.00    [ open record ]  │
│      processed 19 Nov · due 21 Nov · debited ₹0.00                   │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  SYSTEM RECOMMENDATION                       ▾ how it investigated   │
│    ✓ RESOLVE                                                         │
│    Reason: Refund explains the entire ₹13,571.00 discrepancy.        │
│                                                                      │
│    MODEL CONFIDENCE 100% (not proof of correctness)                  │
├──────────────────────────────────────────────────────────────────────┤
│  YOUR DECISION                                                       │
│   [ ✓ Accept ]   [ ✗ Reject ]   [ ↑ Escalate ]                       │
└──────────────────────────────────────────────────────────────────────┘
```

Design decisions:

- **The claim is one sentence in plain language**, and it names the money and
  the consequence. Not "MISSING_REFUND detected."
- **The arithmetic is a decomposition, not a number.** The analyst can see the
  discrepancy being taken apart and reaching zero. This is what makes the
  conclusion checkable in seconds instead of minutes.
- **`unexplained` is always shown, even when zero.** It is the single number
  that separates a resolution from a guess.
- **Every cited record is one click from its source.** Doubt must be cheap.
- **SYSTEM RECOMMENDATION vs YOUR DECISION:** Explicitly separate the AI's recommendation from the human's decision. After the analyst acts, show `✓ ACCEPTED BY YOU` with timestamp/metadata. Do not imply the AI automatically closed the financial case.
- **EVIDENCE SUFFICIENCY vs MODEL CONFIDENCE:** Prefer `EVIDENCE SUFFICIENCY` (e.g. 100/100) for the system's evidence-based assessment. Model confidence sits separately. When a case shows *"MODEL CONFIDENCE = 100%"* but *"EVIDENCE SUFFICIENCY = 0/100"*, that contrast teaches the analyst not to read the model's confidence as proof. The evidence score must be derived from actual verification rules.
- **The tool trail is collapsed by default.** It matters for audit and for the
  demo, but an analyst clearing a queue does not need it every time.
- **Reject captures a reason.** A one-tap "wrong cause / wrong amount / bad
  evidence / actually fine" is the highest-value data this product can collect,
  and it feeds Phase 15's stress cases.
- **Continuous case navigation:** Supports `← Previous Case` and `Next Case →`. Returning to the queue must preserve filters, sorting, current position, scroll position, and selected queue band.
- **Keyboard first.** `A` accept, `R` reject, `E` escalate, `J` next case, `K` previous case, `Esc` close evidence panel. Only implement shortcuts that are safe and practical. Keyboard shortcuts should have discoverable help/tooltips.

### Evidence: a panel, not a screen

The spec lists Evidence as a fifth screen. **I recommend against it.** Making
the analyst navigate away to check a record breaks the judgement they are in
the middle of. It should be a slide-over panel from the citation, showing the
raw record and its settlement lines, dismissable with Escape.

Fewer screens, less navigation, more trust.

---

## Screen 4 — Analytics

**Question it answers:** *is the system actually working?*

This is for a team lead and for the demo, not for daily use. It is where the
honest numbers live:

- detection precision and recall, against ground truth
- classification accuracy
- **false resolution rate — displayed largest**, because it is the metric that
  decides whether any of this is usable
- resolution / review / escalation split
- throughput, and the confidence-overclaim gap

Ground truth is exposed here **as aggregates only**. The API enforces that; the
UI must not try to work around it.

---

## Design rules

**Money**

- Always `₹` with two decimals, right-aligned, tabular figures so columns line
  up. Never a bare number, never a float.
- **Negative (money missing) is red. Positive (surplus) is amber — not green.**
  A surplus means a refund that was never paid out. It is also a problem, and
  colouring it green would teach exactly the wrong instinct.

**Numbers**

- No figure appears without a route to its provenance. If it cannot be traced,
  it should not be rendered.
- Percentages carry their denominator: `100% (700/700)`, never bare `100%`.

**States**

- Escalating must be exactly as fast as accepting. If escalation is slower,
  people accept out of fatigue — and that is the false-resolution path.
- **Loading states:** Do NOT use a full-page generic spinner while loading an investigation. The existing page structure should remain visible while individual sections load (e.g. "Loading evidence records...", "Loading investigation result...", "Loading evaluation metrics..."). Skeleton states should preserve layout and prevent visual jumping. If possible, the previous case shell should remain visible while transitioning to the next case rather than showing a blank page.
- **Empty states:** Say what happened, do not use generic "No data." examples:
  - No escalations: "Nothing escalated in this run."
  - No pending reviews: "No cases are waiting for your review."
  - No matching filters: "No cases match these filters."
  Zero is a valid and positive operational state where appropriate.
- **API failure and partial data states:** Explicit UX rules for failures.
  - E.g., "Unable to load settlement record. The investigation is preserved. No financial decision was made. [ Retry ]"
  - Partial evidence: "Payment found ✓", "Refund found ✓", "Settlement record unavailable ⚠". Then: "AUTOMATIC RESOLUTION DISABLED. Human review required." The UI must never turn missing evidence into a successful conclusion.

**Tone**

- Plain finance language. "Settled short by ₹400", not "negative delta of
  40000 minor units".
- The system never says "I'm confident". It says what it checked, and what it
  could not account for.

---

## What not to build

| Not building | Why |
|---|---|
| A chat box / "ask the AI" | Undermines the controlled-tool architecture — the whole safety story is that the model reaches data only through validated tools |
| Free-text query over financial data | Same reason, plus it invites arbitrary access |
| An editable ledger | This is an investigation surface. Financial records are read-only over HTTP, asserted by test |
| Charts for their own sake | One waterfall on the investigation view earns its place; a pie chart of exception types does not |
| A settings page for thresholds | 90/60 live in config. A UI to change what counts as "resolved" invites gaming the metric |
| Dark/light theme toggle | Cost without judged value at this stage |

---

## Build order

Risk-first, so the thing that matters most is finished earliest:

1. **Investigation View** — the product. Build against `GET /api/exceptions/{id}`,
   which already returns everything it needs.
2. **Exception Queue** — the path to it.
3. **Command Center** — the entry point; simplest of the three.
4. **Analytics** — mostly a table over `GET /api/metrics`.
5. Polish: keyboard shortcuts, empty states, deep links.

If time runs short, 1 and 2 alone still demo the entire product story.

**No live API calls during the final demo.** Every investigation is already stored in
the audit trail; the UI reads it back so the presentation cannot fail because of network connectivity, external API rate limits, or venue Wi-Fi. However, the UI must still use the same API contracts that the real application uses. Do not create a separate fake demo-only frontend. The demo data should come from the application's seeded/test database or stored investigation results.

---

## Demo mapping

| Beat | Screen |
|---|---|
| "10,055 payments, 9,149 reconciled" | Command Center |
| "700 exceptions, here's what needs a human" | Command Center → queue split |
| "Let's open one" | Queue, sorted by money at risk |
| "It found the over-deducted fee — and here's the arithmetic" | Investigation View |
| "Here's every record it looked at, and the tool calls" | Trail, expanded |
| "This one it couldn't explain — so it escalated" | An `UNKNOWN_DISCREPANCY` case |
| "The model said 100% certain. The system scored it 0." | Same case, score panel |
| "And the numbers" | Analytics |

That sixth and seventh beat is the one that wins trust. Rehearse it.

---

# Visual language

## Why the palette is constrained, not chosen

Three semantics are already fixed by the product:

| Meaning | Colour |
|---|---|
| Money missing (negative delta) | red |
| Surplus (a refund never paid out) | amber |
| Verified · balanced · chain intact | green |

So the brand accent **cannot** be green, red or amber — a brand green would
collide with "verified" and destroy the signal. That leaves blue/navy/slate,
which is also what credible finance tooling looks like. The palette falls out
of the semantics rather than being picked.

## Light by default

Finance tools are used in daylight, next to a spreadsheet. Light also actively
separates this from the dark-hero-plus-neon look that reads as *AI demo*. Dark
mode is optional polish, not the default.

## Tokens

```css
:root {
  /* Canvas — not pure white; pure white behind dense tables is harsh */
  --canvas:        #FAFAFB;
  --surface:       #FFFFFF;
  --surface-sunk:  #F4F5F7;   /* table headers, code blocks */
  --border:        #E4E7EC;   /* 1px hairlines, not shadows */
  --border-strong: #D0D5DD;

  /* Ink — navy-black rather than true black; softer, more considered */
  --ink:           #0F2A43;
  --ink-secondary: #4A5A6B;
  --ink-muted:     #8593A3;

  /* Brand — deep navy for chrome, one brighter blue for interaction.
     Adjacent to payments-industry navy without imitating any one brand. */
  --brand:         #14346B;
  --brand-hover:   #0F2851;
  --interactive:   #2563EB;
  --interactive-bg:#EFF4FF;

  /* Semantics — one step darker than the usual palette defaults.
     Serious, not alarm-clock. */
  --negative:      #B42318;   /* money missing */
  --negative-bg:   #FEF3F2;
  --attention:     #B54708;   /* surplus, review, escalated */
  --attention-bg:  #FFFAEB;
  --verified:      #067647;   /* verified, balanced, intact — never brand */
  --verified-bg:   #ECFDF3;
  --neutral:       #475467;   /* pending, no action needed */
  --neutral-bg:    #F2F4F7;

  --radius:        6px;       /* restrained; 16px+ reads consumer app */
  --radius-lg:     8px;
}

/* Optional dark mode */
:root[data-theme="dark"] {
  --canvas:        #0B1220;
  --surface:       #111A2B;
  --surface-sunk:  #0D1524;
  --border:        #1E2A3D;
  --ink:           #E6EAF2;
  --ink-secondary: #9AA8BC;
  --ink-muted:     #6B7A90;
  --interactive:   #6098FF;
  --negative:      #F97066;
  --attention:     #F79009;
  --verified:      #47CD89;
}
```

## Where each colour is allowed

| Token | Used for | Never used for |
|---|---|---|
| `--brand` | header, active nav, primary button | data values |
| `--interactive` | links, record citations, focus rings | status |
| `--negative` | negative money, missing settlement, broken chain | generic errors in forms |
| `--attention` | surplus money, REVIEW, ESCALATED | anything positive |
| `--verified` | ✓ verified evidence, balanced batches, intact chain | brand, buttons, decoration |
| `--neutral` | matched, pending, "no action" | anything needing attention |

**Status colours are never decorative.** If a badge is green, it is because
code verified something.

## Typography

| | |
|---|---|
| Interface | Inter (or system stack) — 14px base, 13px in tables |
| **Money and IDs** | **tabular numerals, right-aligned** — `font-variant-numeric: tabular-nums` |
| Record IDs | monospace, `--ink-secondary`, `--interactive` on hover |

Money columns that don't line up digit-for-digit read as amateur to anyone who
works with numbers. This single detail does more for credibility than any
colour choice.

## Surfaces

- **1px hairline borders, not drop shadows.** Shadows read consumer; hairlines
  read ledger.
- Cards: `--surface` on `--canvas`, `1px solid --border`, `--radius`.
- Row hover: `--surface-sunk`. No lift, no scale, no glow.
- Focus: 2px `--interactive` ring. Visible, because this screen is keyboard-driven.

## What would make it look like an AI site — avoid all of it

| Avoid | Why |
|---|---|
| Violet→blue gradients | the single strongest "AI demo" tell |
| Glassmorphism, backdrop blur | decorative, hurts dense-table legibility |
| Glowing borders, neon accents | undermines a product whose pitch is restraint |
| Dark hero with a bright accent | reads as landing page, not as tool |
| `rounded-3xl` everywhere | consumer app, not finance |
| Animated gradient text | no |
| More than one accent colour | dilutes every signal in the table |

## The one-line test

> If a screenshot could sit beside a real payments dashboard without looking
> like a hackathon project — it's right. If it looks like a landing page for an
> AI startup — start over.

## Responsive Design

Primary target: Desktop/laptop finance operations environment (Reference layouts: 1440×900, 1280×800). The application must also remain usable on tablet and mobile.

- **Desktop**: Sidebar navigation, dense investigation layout, evidence sections can appear side-by-side where appropriate.
- **Tablet**: Reduce horizontal density, stack evidence sections when required.
- **Mobile**: Stack all major sections vertically. Tables should become horizontally scrollable or transform into readable cards where appropriate. No horizontal page overflow. Actions remain accessible.

Do not compromise the desktop analyst workflow merely to make the application mobile-first.

## Accessibility

- Keyboard navigation and visible focus states.
- Semantic HTML and accessible buttons/tables.
- Sufficient colour contrast.
- Icons must not be the only way status is communicated; status must never rely on colour alone.
- Appropriate aria-labels for icon-only controls.
- Keyboard shortcuts should have discoverable help/tooltips.
- `Esc` should close slide-over evidence panels.
- Focus should be managed correctly when opening/closing panels.

Status examples should include both icon/text and colour:
✓ VERIFIED, ⚠ REVIEW, ✕ REJECTED, ↑ ESCALATED

## Frontend Implementation Constraints

- Frontend must consume backend APIs.
- Financial reconciliation logic belongs to the backend, not React/Next.js UI code.
- Frontend must not independently calculate authoritative settlement results.
- Financial records are read-only.
- Do not invent financial data, API responses, fake production metrics, or fabricate confidence/evidence scores.
- Development mocks are allowed only when explicitly marked as mocks and must be replaceable by real API responses.
- UI must not claim RESOLVED unless the backend provides a resolved decision.
- UI must not claim evidence was verified unless backend evidence verification supports it.
- UI must not treat LLM confidence as evidence.
- All monetary values must be represented and displayed in INR with two decimal places.
- Money should use tabular numerals and be right-aligned in tables.
- IDs should use monospace styling.
- Financial records should have a clear provenance path.

## Non-negotiable product principles

1. **Evidence before explanation.** 
2. **Money before metrics.** 
3. **Every conclusion must be traceable.** 
4. **AI recommendation is not human approval.** 
5. **Model confidence is never proof.** 
6. **Unexplained money must always be visible.** 
7. **Escalation must be as easy as acceptance.** 
8. **Financial records are read-only.** 
9. **No financial decision without an auditable record.** 
10. **When evidence is insufficient, the system stops.** 
11. **The UI must never hide uncertainty.** 
12. **Every important financial number must have a provenance path.** 
