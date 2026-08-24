"""G8 - exception injection.

The contract has two halves, and both matter:

* every delta-visible injection must be detectable by arithmetic, and
* nothing else may be. A spurious exception means an injector is producing an
  accounting artifact rather than a fault, and it would silently corrupt every
  precision figure the project later reports.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from backend.config import FinancialConfig
from backend.enums import ExceptionType, PaymentStatus, SettlementStatus
from backend.generation.exceptions import (
    DEFAULT_MIX,
    FAMILY_CROSS_ENTITY,
    FAMILY_MULTI_CAUSE,
    FAMILY_TIMING_SHIFTED,
    INJECTORS,
    inject_exceptions,
)
from backend.generation.generator import generate_world
from backend.generation.verify import batch_residuals, reconcile_world

AS_OF = date(2026, 1, 31)


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return FinancialConfig()


@pytest.fixture(scope="module")
def injected(cfg_mod):
    world = generate_world(seed=11, n_payments=3_000, as_of=AS_OF, cfg=cfg_mod)
    report = inject_exceptions(
        world, cfg=cfg_mod, as_of=AS_OF, seed=11, rate_bps=700
    )
    return world, report


def _truths(world):
    return {t["payment_id"]: t for t in world.truths}


def _visible(world):
    return {
        pid
        for pid, t in _truths(world).items()
        if t["is_exception"] and (t["injection_params"] or {}).get("delta_visible")
    }


# --- the headline contract ------------------------------------------------
def test_every_delta_visible_injection_is_detected(injected, cfg_mod):
    world, _ = injected
    detected = {m.payment_id for m in reconcile_world(world, cfg_mod, AS_OF)["mismatches"]}
    assert _visible(world) - detected == set(), "injected fault went undetected"


def test_no_exception_appears_that_was_not_injected(injected, cfg_mod):
    """The one that protects every precision number the project reports."""
    world, _ = injected
    detected = {m.payment_id for m in reconcile_world(world, cfg_mod, AS_OF)["mismatches"]}
    assert detected - _visible(world) == set(), "reconciler found a fault nobody injected"


def test_batches_still_balance_after_injection(injected):
    """Breaking a payment must not break the accounting identity around it."""
    world, _ = injected
    assert batch_residuals(world) == []


# --- coverage and rate ----------------------------------------------------
def test_all_thirteen_injectors_are_exercised(injected):
    _, report = injected
    assert set(report.injected) == set(INJECTORS) == set(DEFAULT_MIX)
    assert all(count > 0 for count in report.injected.values())


def test_injection_rate_matches_the_request(injected):
    world, report = injected
    rate_bps = report.total() * 10_000 // len(world.payments)
    assert 600 <= rate_bps <= 800, f"asked for 700bps, produced {rate_bps}"


def test_the_hard_families_are_a_meaningful_share(injected):
    """If these are rare, the AI's advantage cannot be measured."""
    _, report = injected
    hard = sum(
        report.injected[f]
        for f in (FAMILY_MULTI_CAUSE, FAMILY_CROSS_ENTITY, FAMILY_TIMING_SHIFTED)
    )
    assert hard * 100 // report.total() >= 15


def test_the_vast_majority_of_payments_remain_clean(injected, cfg_mod):
    world, _ = injected
    counts = reconcile_world(world, cfg_mod, AS_OF)["counts"]
    clean_share = counts["MATCHED"] * 100 // len(world.payments)
    assert 90 <= clean_share <= 96


# --- ground truth quality -------------------------------------------------
def test_ground_truth_records_a_reason_for_every_exception(injected):
    world, _ = injected
    for truth in _truths(world).values():
        if truth["is_exception"]:
            assert truth["reason_code"]
            assert truth["notes"]
            assert truth["injection_params"]


def test_unknown_discrepancies_carry_no_explanation(injected):
    """The category the agent must refuse to close.

    If ground truth held an explanation, an honest 'I don't know' would be
    scored as a failure - which would train the whole system to guess.
    """
    world, _ = injected
    unknowns = [
        t
        for t in _truths(world).values()
        if t["reason_code"] == ExceptionType.UNKNOWN_DISCREPANCY.value
    ]
    assert unknowns
    assert all(t["explained_amount"] is None for t in unknowns)


def test_explained_amounts_are_present_for_explainable_causes(injected):
    world, _ = injected
    for truth in _truths(world).values():
        if truth["is_exception"] and truth["reason_code"] != (
            ExceptionType.UNKNOWN_DISCREPANCY.value
        ):
            assert truth["explained_amount"] is not None


def test_multi_cause_records_its_components(injected):
    """Decomposition is the point; ground truth has to score it."""
    world, _ = injected
    multi = [
        t for t in _truths(world).values() if t["reason_code"] == FAMILY_MULTI_CAUSE
    ]
    assert multi
    for truth in multi:
        components = truth["injection_params"]["components"]
        assert len(components) == 2
        assert sum(components.values()) == truth["explained_amount"]


def test_duplicate_payment_flags_the_twin_not_the_original(injected):
    """The original charge is blameless; flagging it would inflate every count."""
    world, _ = injected
    truths = _truths(world)
    duplicates = [
        t
        for t in truths.values()
        if t["reason_code"] == ExceptionType.DUPLICATE_PAYMENT.value
    ]
    assert duplicates
    for truth in duplicates:
        original = truth["injection_params"]["duplicate_of"]
        assert truths[original]["is_exception"] is False


# --- individual injectors behave as documented ----------------------------
def test_missing_settlement_removes_the_line(injected):
    world, _ = injected
    settled = {
        i["payment_id"] for i in world.settlement_items if i["item_type"] == "PAYMENT"
    }
    for pid, truth in _truths(world).items():
        if truth["reason_code"] == ExceptionType.MISSING_SETTLEMENT.value:
            assert pid not in settled


def test_partial_settlement_leaves_a_remainder_in_an_unprocessed_batch(injected):
    world, _ = injected
    batches = {s["settlement_id"]: s for s in world.settlements}
    for pid, truth in _truths(world).items():
        if truth["reason_code"] != ExceptionType.PARTIAL_SETTLEMENT.value:
            continue
        pending = batches[truth["injection_params"]["pending_batch"]]
        assert pending["status"] == SettlementStatus.CREATED.value
        assert pending["settlement_date"].date() > AS_OF


def test_cross_entity_explanation_is_unreachable_by_payment_id(injected):
    """The hop a rule keyed on `adjustment.payment_id` cannot make."""
    world, _ = injected
    adjustments = {a["adjustment_id"]: a for a in world.adjustments}
    hits = 0
    for truth in _truths(world).values():
        if truth["reason_code"] != FAMILY_CROSS_ENTITY:
            continue
        adjustment = adjustments[truth["injection_params"]["adjustment_id"]]
        assert adjustment["payment_id"] is None
        assert adjustment["settlement_id"]
        hits += 1
    assert hits


def test_timing_shifted_debit_exists_but_is_not_yet_paid(injected):
    """Looks identical to a missing refund until you check batch status."""
    world, _ = injected
    batches = {s["settlement_id"]: s for s in world.settlements}
    for truth in _truths(world).values():
        if truth["reason_code"] != FAMILY_TIMING_SHIFTED:
            continue
        pending = batches[truth["injection_params"]["pending_batch"]]
        assert pending["status"] == SettlementStatus.CREATED.value


def test_unexpected_adjustment_has_no_stated_reason(injected):
    world, _ = injected
    adjustments = {a["adjustment_id"]: a for a in world.adjustments}
    for truth in _truths(world).values():
        if truth["reason_code"] != ExceptionType.UNEXPECTED_ADJUSTMENT.value:
            continue
        assert adjustments[truth["injection_params"]["adjustment_id"]]["reason"] is None


def test_injection_is_reproducible(cfg_mod):
    def run(seed):
        w = generate_world(seed=3, n_payments=800, as_of=AS_OF, cfg=cfg_mod)
        r = inject_exceptions(w, cfg=cfg_mod, as_of=AS_OF, seed=seed, rate_bps=700)
        return w, r

    a_world, a_report = run(3)
    b_world, b_report = run(3)
    assert a_report.injected == b_report.injected
    assert a_world.truths == b_world.truths


def test_failed_and_pending_payments_are_never_injected(injected):
    """Injecting into an unsettled payment would be indistinguishable from
    the legitimate pending state, corrupting the timing statistics."""
    world, _ = injected
    payments = {p["payment_id"]: p for p in world.payments}
    for pid, truth in _truths(world).items():
        if truth["is_exception"]:
            assert payments[pid]["status"] != PaymentStatus.FAILED.value
