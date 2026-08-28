"""Run the AI investigator over open exceptions.

    python scripts/investigate.py --limit 5 --show      # prove it works
    python scripts/investigate.py --limit 100           # the real run

Quota discipline is built in, not optional:

* **Checkpointed.** Exceptions already investigated are skipped, so a run that
  dies at case 87 resumes at 88 instead of respending 87 requests.
* **Cached.** Every raw model response is stored on disk. Re-running the same
  case replays for free; only genuinely new work costs quota.
* **Throttled.** The client paces itself under the per-minute ceiling.
* **Budgeted.** `--max-requests` stops the run before it can eat the day's
  allowance.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from backend.agents.investigator import investigate, persist
from backend.agents.llm import LLMError, ResponseCache, build_provider
from backend.config import get_settings
from backend.db.session import agent_engine, owner_engine
from backend.money import format_paise


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    p = argparse.ArgumentParser(description="Investigate open exceptions with the AI agent")
    p.add_argument("--limit", type=int, default=5, help="how many exceptions")
    p.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of_date)
    p.add_argument("--run-id", default=None, help="defaults to the latest recon run")
    p.add_argument("--exception-id", default=None, help="investigate exactly one")
    p.add_argument("--type", dest="types", action="append",
                   help="restrict to an exception_type (repeatable)")
    p.add_argument("--stratified", action="store_true",
                   help="spread the sample evenly across exception types")
    p.add_argument("--show", action="store_true", help="print the full trace")
    p.add_argument("--max-requests", type=int, default=200,
                   help="stop before spending more than this many API requests")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--redo", action="store_true",
                   help="re-investigate cases that already have a result")
    return p.parse_args()


def pick(conn, args) -> list[dict]:
    """Choose exceptions, skipping any already investigated."""
    run_id = args.run_id or conn.execute(
        text("SELECT run_id FROM recon.recon_runs ORDER BY started_at DESC LIMIT 1")
    ).scalar()
    if not run_id:
        raise SystemExit("no reconciliation run found — run scripts/reconcile.py first")

    where = ["e.run_id = :r"]
    params: dict = {"r": run_id}
    if args.exception_id:
        where.append("e.exception_id = :eid")
        params["eid"] = args.exception_id
    if args.types:
        where.append("e.exception_type = ANY(:types)")
        params["types"] = args.types
    if not args.redo:
        # The checkpoint: never pay twice for the same case.
        where.append(
            "NOT EXISTS (SELECT 1 FROM recon.investigations i"
            "            WHERE i.exception_id = e.exception_id)"
        )

    rows = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT e.exception_id, e.payment_id, e.delta, e.exception_type"
                f"  FROM recon.exceptions e WHERE {' AND '.join(where)}"
                # Discrepancy cases first: they are the ones with something to
                # investigate, and the ones the baseline comparison turns on.
                " ORDER BY abs(e.delta) DESC, e.exception_id"
            ),
            params,
        ).mappings()
    ]

    if args.stratified and not args.exception_id:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[row["exception_type"]].append(row)
        spread, i = [], 0
        while len(spread) < args.limit and any(buckets.values()):
            for kind in sorted(buckets):
                if buckets[kind] and len(spread) < args.limit:
                    spread.append(buckets[kind].pop(0))
            i += 1
            if i > 1000:
                break
        return spread, run_id
    return rows[: args.limit], run_id


def live_step(step) -> None:
    """Print each step the moment it happens, so a slow case is visible."""
    if step.step_type == "llm_call":
        note = f" — {step.observation[:60]}" if step.observation else ""
        print(f"      [{step.seq:>2}] model ({step.duration_ms}ms){note}", flush=True)
    elif step.step_type == "tool_call":
        args = ", ".join(f"{k}={v}" for k, v in (step.tool_args or {}).items())
        bad = "  ✗ " + (step.observation or "") if step.observation else ""
        print(f"      [{step.seq:>2}] → {step.tool_name}({args}) {step.duration_ms}ms{bad}",
              flush=True)
    elif step.step_type == "finding":
        print(f"      [{step.seq:>2}] ✓ submit_finding", flush=True)
    else:
        print(f"      [{step.seq:>2}] ⚠ {step.observation}", flush=True)


def _score_lines(result) -> list[str]:
    """The deductions, so a reviewer sees why a case scored what it did."""
    out = [f"{'score':<40}{100:>6}   starting"]
    for f in result.score_factors:
        out.append(f"{'  ' + f.name:<40}{f.delta:>+6}   {f.detail[:60]}")
    return out


def show_trace(result) -> None:
    print(f"  {'─' * 66}")
    print(f"  cause      : {result.cause_type}")
    print(f"  summary    : {result.summary[:200]}")
    for c in result.evidence:
        print(f"    evidence : {c.record_type:<16}{c.record_id:<22}"
              f"{format_paise(c.claimed):>12}   ✓ verified")
    for c in result.rejected_evidence:
        print(f"    REJECTED : {c.record_type:<16}{c.record_id:<22}"
              f"{format_paise(c.claimed):>12}")
        print(f"               {c.reason}")
    print(f"  delta      : {format_paise(result.delta)}")
    print(f"  unexplained: {format_paise(result.unexplained_amount or 0)}")
    print()
    for line in result.score_factors and _score_lines(result) or []:
        print(f"  {line}")
    print(f"  status     : {result.final_status}"
          f"   evidence score {result.evidence_score}"
          f"   (model claimed {result.reasoning_confidence}%)")


def main() -> int:
    args = parse_args()
    settings = get_settings()
    cfg = settings.financial()
    cache = ResponseCache(enabled=not args.no_cache)

    try:
        provider = build_provider(settings, cache=cache)
    except LLMError as exc:
        print(f"cannot start: {exc}")
        return 1

    engine = agent_engine()
    with engine.connect() as conn:
        selected, run_id = pick(conn, args)

    print(f"\nAI investigator — {provider.model} (prompt {__import__('backend.agents.prompts', fromlist=['x']).PROMPT_VERSION})")
    print(f"  run {run_id} | as-of {args.as_of}")
    print(f"  {len(selected)} exception(s) to investigate"
          f" | budget {args.max_requests} requests | cache {'off' if args.no_cache else 'on'}\n")
    if not selected:
        print("nothing to do — every selected exception already has a result.")
        return 0

    outcomes: dict[str, int] = defaultdict(int)
    correct_shape = 0
    #: (overclaim, claimed, computed) - evidence that the model's own
    #: confidence is not a number anything should act on.
    confidence_gaps: list[tuple[int, int, int]] = []

    for n, row in enumerate(selected, 1):
        if provider.requests_made >= args.max_requests:
            print(f"\nstopping: request budget ({args.max_requests}) reached. "
                  f"Re-run to continue from here — completed work is not repeated.")
            break

        print(f"[{n}/{len(selected)}] {row['exception_id']}  {row['payment_id']}  "
              f"{format_paise(row['delta']):>12}  (classified {row['exception_type']})")

        with engine.connect() as conn:
            result = investigate(
                conn, provider,
                exception_id=row["exception_id"],
                payment_id=row["payment_id"],
                delta=row["delta"],
                cfg=cfg, as_of=args.as_of,
                max_tool_calls=settings.llm_max_tool_calls,
                # Only rule-detected cases get the hint; see opening_message.
                rule_flag=row["exception_type"] if row["delta"] == 0 else None,
                on_step=live_step if args.show else None,
            )
            persist(conn, result)
            conn.commit()

        # The agent role is read-only on `exceptions` by design, so the case
        # status is written back by the owner. The agent records what it found;
        # it does not get to close its own ticket.
        with owner_engine().begin() as writer:
            writer.execute(
                text(
                    "UPDATE recon.exceptions SET status = :s, evidence_score = :e"
                    " WHERE exception_id = :i"
                ),
                {"s": result.final_status, "e": result.evidence_score,
                 "i": row["exception_id"]},
            )

        outcomes[result.final_status] += 1
        correct_shape += int(result.cause_type == row["exception_type"])
        if result.reasoning_confidence is not None and result.evidence_score is not None:
            confidence_gaps.append(
                (result.reasoning_confidence - result.evidence_score,
                 result.reasoning_confidence, result.evidence_score)
            )
        if args.show:
            show_trace(result)
        else:
            print(f"      → {result.final_status:<10} score {result.evidence_score:>3}"
                  f"  {result.cause_type:<24}"
                  f" unexplained {format_paise(result.unexplained_amount or 0)}"
                  f"  ({result.tool_call_count} tools)")

    done = sum(outcomes.values())
    print(f"\n{'═' * 72}")
    print(f"  investigated        {done}")
    for status, count in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        print(f"    {status:<18}{count:>5}")
    print(f"  agreed with the rule classifier   {correct_shape}/{done}")
    if confidence_gaps:
        worst = max(confidence_gaps)
        print(f"\n  model's self-assessment vs computed score")
        print(f"    largest overclaim   {worst[0]} points "
              f"(said {worst[1]}%, scored {worst[2]})")
        print(f"    mean overclaim      "
              f"{sum(g[0] for g in confidence_gaps) / len(confidence_gaps):.0f} points")
    print(f"\n  API requests spent  {provider.requests_made}")
    print(f"  cache hits          {cache.hits}")
    print(f"  throttle wait       {provider.throttled_seconds:.1f}s")
    print(f"{'═' * 72}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
