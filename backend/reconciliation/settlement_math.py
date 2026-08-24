"""The settlement calculation and reconciliation decision - pure functions.

These take plain frozen dataclasses, never ORM objects, so they can be tested
and property-checked without a database. Phase 5's engine is responsible for
loading rows and packing them into ``PaymentFacts``; it owns no arithmetic.

The formula (Phase 1, Part E), all integers, all paise::

    EXPECTED_NET = gross - fee_retained - tax_retained - refunded + adjusted
    ACTUAL_NET   = sum of settlement item net_amounts resolving to the payment
    DELTA        = ACTUAL_NET - EXPECTED_NET

DELTA is the currency of the entire product. Every investigation, deterministic
or AI, is the same task: decompose DELTA into evidenced components and report
whatever refuses to decompose as ``unexplained_amount``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Sequence

from backend.config import FinancialConfig
from backend.enums import (
    SETTLEABLE_PAYMENT_STATUSES,
    DataCondition,
    PaymentMethod,
    PaymentStatus,
    ReconStatus,
    RefundStatus,
)
from backend.money import round_half_up
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.timing import settlement_deadline, settlement_eligible_on

# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefundFact:
    refund_id: str
    amount: int
    status: RefundStatus
    #: When the refund was processed. A refund settles on its own T+2 cycle
    #: from this date, which may fall in a later batch than the payment's.
    processed_at: date | datetime | None = None

    @property
    def is_settled_debit(self) -> bool:
        """Only processed refunds actually leave the merchant's balance."""
        return RefundStatus(self.status) is RefundStatus.PROCESSED


@dataclass(frozen=True, slots=True)
class AdjustmentFact:
    adjustment_id: str
    amount: int  # signed: credit positive, debit negative
    type: str
    created_at: date | datetime | None = None


@dataclass(frozen=True, slots=True)
class PaymentFacts:
    """Everything the calculator needs about one payment, already loaded."""

    payment_id: str
    amount: int
    method: PaymentMethod
    status: PaymentStatus
    captured_at: date | datetime | None = None
    #: ``None`` means no fee row exists at all - a data condition, distinct
    #: from a fee of zero (which is legitimate for UPI).
    fee: int | None = None
    tax: int | None = None
    refunds: Sequence[RefundFact] = field(default_factory=tuple)
    adjustments: Sequence[AdjustmentFact] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpectedSettlement:
    """Fully itemised expectation - never just a bare number.

    Every field here becomes a line in the evidence package (Phase 8), which is
    why the intermediate quantities are retained rather than collapsed.
    """

    payment_id: str
    gross: int
    fee_charged: int
    tax_charged: int
    fee_reversed: int
    tax_reversed: int
    refunded: int
    adjusted: int
    expected_net: int
    flags: tuple[DataCondition, ...] = ()
    #: Components excluded because their own settlement cycle has not yet
    #: elapsed. Not discrepancies - money correctly still in flight. Surfaced
    #: so the UI can say "Rs400 refund not yet debited" instead of going quiet.
    refunds_not_yet_due: int = 0
    adjustments_not_yet_due: int = 0

    @property
    def fee_retained(self) -> int:
        return self.fee_charged - self.fee_reversed

    @property
    def tax_retained(self) -> int:
        return self.tax_charged - self.tax_reversed

    def components(self) -> dict[str, int]:
        """Signed contributions that sum exactly to ``expected_net``."""
        return {
            "gross": self.gross,
            "fee_retained": -self.fee_retained,
            "tax_retained": -self.tax_retained,
            "refunded": -self.refunded,
            "adjusted": self.adjusted,
        }


@dataclass(frozen=True, slots=True)
class ReconOutcome:
    payment_id: str
    status: ReconStatus
    expected_net: int
    actual_net: int
    delta: int
    flags: tuple[DataCondition, ...] = ()

    @property
    def is_exception(self) -> bool:
        return self.status is ReconStatus.EXCEPTION


# --------------------------------------------------------------------------
# Expected settlement
# --------------------------------------------------------------------------


def component_is_due(
    event_at: date | datetime | None, as_of: date | None, cfg: FinancialConfig
) -> bool:
    """Has this component's own settlement cycle elapsed by ``as_of``?

    Refunds and adjustments settle on their own T+2 cycle. Between a refund
    being processed and its batch landing, the money is correctly still in
    flight - counting it as expected would manufacture a discrepancy out of
    healthy data.

    The cutoff is the **eligibility** date, not the deadline. The grace period
    answers a different question - "is this late enough to call missing?" - and
    using it here would exclude a refund that has demonstrably already been
    debited, producing a phantom discrepancy on healthy data.

    ``as_of=None`` disables the cutoff and counts everything.
    """
    if as_of is None or event_at is None:
        return True
    return settlement_eligible_on(event_at, cfg) <= as_of


def expected_net_settlement(
    facts: PaymentFacts, cfg: FinancialConfig, as_of: date | None = None
) -> ExpectedSettlement:
    """Compute what this payment *should* net into the merchant's bank.

    When ``as_of`` is given, refunds and adjustments whose own settlement cycle
    has not yet elapsed are excluded from the expectation and reported
    separately. The payment leg is not gated here - its pending state is
    decided by :func:`reconcile_payment`.
    """
    flags: list[DataCondition] = []

    settleable = PaymentStatus(facts.status) in SETTLEABLE_PAYMENT_STATUSES
    if not settleable:
        flags.append(DataCondition.NON_SETTLEABLE_STATUS)
    gross = facts.amount if settleable else 0

    if settleable and facts.fee is None:
        flags.append(DataCondition.MISSING_FEE_RECORD)

    # A payment that never captured is never charged a fee, even if a stray fee
    # row exists for it. Carrying the fee through would make the merchant owe
    # money on a payment they never received - the flag is the right signal, a
    # negative expectation is not.
    fee_charged = (facts.fee or 0) if settleable else 0
    tax_charged = (facts.tax or 0) if settleable else 0

    # Only processed refunds have actually moved money, and only those whose
    # own settlement cycle has elapsed are expected to have been debited yet.
    settled_refunds = [r for r in facts.refunds if r.is_settled_debit]
    refunded = sum(
        r.amount
        for r in settled_refunds
        if component_is_due(r.processed_at, as_of, cfg)
    )
    refunds_not_yet_due = sum(
        r.amount
        for r in settled_refunds
        if not component_is_due(r.processed_at, as_of, cfg)
    )
    # The over-refund check uses every processed refund, due or not: an
    # impossible refund total is impossible regardless of when it lands.
    if gross and sum(r.amount for r in settled_refunds) > gross:
        flags.append(DataCondition.REFUND_EXCEEDS_PAYMENT)

    fee_reversed, tax_reversed = _reversal(gross, refunded, fee_charged, tax_charged, cfg)

    adjusted = sum(
        a.amount
        for a in facts.adjustments
        if component_is_due(a.created_at, as_of, cfg)
    )
    adjustments_not_yet_due = sum(
        a.amount
        for a in facts.adjustments
        if not component_is_due(a.created_at, as_of, cfg)
    )

    expected_net = (
        gross
        - (fee_charged - fee_reversed)
        - (tax_charged - tax_reversed)
        - refunded
        + adjusted
    )

    return ExpectedSettlement(
        payment_id=facts.payment_id,
        gross=gross,
        fee_charged=fee_charged,
        tax_charged=tax_charged,
        fee_reversed=fee_reversed,
        tax_reversed=tax_reversed,
        refunded=refunded,
        adjusted=adjusted,
        expected_net=expected_net,
        flags=tuple(flags),
        refunds_not_yet_due=refunds_not_yet_due,
        adjustments_not_yet_due=adjustments_not_yet_due,
    )


def _reversal(
    gross: int, refunded: int, fee: int, tax: int, cfg: FinancialConfig
) -> tuple[int, int]:
    """Fee/GST credited back on refund, pro-rata to the refunded fraction.

    Returns ``(0, 0)`` under the Phase 1 default (fee retained by the gateway).
    A full refund reverses the fee exactly, with no rounding residue.
    """
    if not cfg.reverse_fee_on_refund or refunded <= 0 or gross <= 0:
        return 0, 0
    if refunded >= gross:
        return fee, tax
    ratio = Decimal(refunded) / Decimal(gross)
    return round_half_up(Decimal(fee) * ratio), round_half_up(Decimal(tax) * ratio)


def check_fee_schedule(
    facts: PaymentFacts, cfg: FinancialConfig
) -> tuple[tuple[DataCondition, ...], int, int]:
    """Compare the recorded fee/tax against the schedule.

    Returns ``(flags, fee_delta, tax_delta)`` where a delta is
    ``expected - recorded`` - i.e. the amount by which the merchant was
    over-charged. Phase 6 turns these into FEE_MISMATCH / TAX_MISMATCH.
    """
    breakdown = compute_fee_and_tax(facts.amount, facts.method, cfg)
    fee_delta = breakdown.fee - (facts.fee or 0)
    tax_delta = breakdown.tax - (facts.tax or 0)
    flags: list[DataCondition] = []
    if fee_delta != 0:
        flags.append(DataCondition.FEE_NOT_PER_SCHEDULE)
    if tax_delta != 0:
        flags.append(DataCondition.TAX_NOT_PER_SCHEDULE)
    return tuple(flags), fee_delta, tax_delta


# --------------------------------------------------------------------------
# Settlement item arithmetic - the single place signs are decided
# --------------------------------------------------------------------------


def payment_item_net(credit: int, fee: int, tax: int) -> int:
    """Net of a PAYMENT settlement line: credit less the gateway's deductions."""
    return credit - fee - tax


def refund_item_net(refund_amount: int) -> int:
    """Net of a REFUND settlement line. Always a debit, hence never positive."""
    if refund_amount < 0:
        raise ValueError(
            f"refund amounts are stored unsigned; got {refund_amount}. "
            "The sign is applied here, exactly once."
        )
    return -refund_amount


def adjustment_item_net(signed_amount: int) -> int:
    """Net of an ADJUSTMENT line. Adjustments carry their own sign in the data."""
    return signed_amount


def batch_net(item_nets: Iterable[int]) -> int:
    """Net payout of a settlement batch. Exact integer sum - no tolerance."""
    return sum(item_nets)


# --------------------------------------------------------------------------
# The reconciliation decision
# --------------------------------------------------------------------------


def reconcile_payment(
    expected: ExpectedSettlement,
    actual_net: int,
    *,
    has_settled_items: bool,
    captured_at: date | datetime | None,
    as_of: date,
    cfg: FinancialConfig,
) -> ReconOutcome:
    """Decide MATCHED / PENDING_SETTLEMENT / EXCEPTION for one payment."""
    delta = actual_net - expected.expected_net
    tolerance = cfg.tolerance_paise

    if abs(delta) <= tolerance:
        # The expectation was met. This is the only test for a match, and it
        # deliberately does not care whether a payment line exists: a payment
        # that owed nothing and received nothing reconciles just as cleanly as
        # one that owed and received.
        #
        # An earlier version treated "nothing was owed" as sufficient on its
        # own. That silently passed a fully-refunded zero-fee payment whose
        # credit line had vanished while its refund debit remained - the
        # expectation was Rs0, so it matched, while Rs2,900 had left the
        # account with nothing behind it. Comparing the delta closes that hole.
        status = ReconStatus.MATCHED
    elif (
        not has_settled_items
        and captured_at is not None
        and as_of <= settlement_deadline(captured_at, cfg)
    ):
        # Money legitimately still in flight - not late, not an exception.
        status = ReconStatus.PENDING_SETTLEMENT
    else:
        status = ReconStatus.EXCEPTION

    return ReconOutcome(
        payment_id=expected.payment_id,
        status=status,
        expected_net=expected.expected_net,
        actual_net=actual_net,
        delta=delta,
        flags=expected.flags,
    )


def reconcile_batch(settlement_net_amount: int, item_nets: Iterable[int]) -> int:
    """Batch-level residual. Must be exactly zero - no tolerance is allowed here,
    because a batch is an arithmetic identity, not a comparison of two sources."""
    return settlement_net_amount - batch_net(item_nets)
