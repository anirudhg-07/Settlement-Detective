"""Response models.

Money crosses the API boundary as **both** integer paise and a formatted string.
The paise value is what a client should compute with; the string is what it
should display. Sending a float would reintroduce, at the last step, exactly the
rounding problem the whole financial model is built to avoid.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.money import format_paise


class Money(BaseModel):
    paise: int
    display: str

    @classmethod
    def of(cls, paise: int | None) -> "Money | None":
        return None if paise is None else cls(paise=paise, display=format_paise(paise))


class Health(BaseModel):
    status: str
    database: bool
    dataset_loaded: bool
    payments: int
    latest_run: str | None


class RunSummary(BaseModel):
    run_id: str
    as_of_date: date
    started_at: datetime
    completed_at: datetime | None
    records_processed: int
    matched: int
    pending: int
    exceptions: int
    batches_checked: int
    batches_out_of_balance: int
    match_rate_bps: int = Field(description="matched + pending, per 10,000")
    tolerance_paise: int


class ExceptionSummary(BaseModel):
    exception_id: str
    payment_id: str
    exception_type: str | None
    status: str
    detected_by: str
    expected_net: Money
    actual_net: Money
    delta: Money
    evidence_score: int | None
    created_at: datetime


class ExceptionPage(BaseModel):
    items: list[ExceptionSummary]
    total: int
    limit: int
    offset: int


class EvidenceItem(BaseModel):
    record_type: str
    record_id: str
    role: str
    amount_contribution: Money | None
    note: str | None


class StepItem(BaseModel):
    seq: int
    step_type: str
    tool_name: str | None
    tool_args: dict | None
    observation: str | None
    duration_ms: int | None


class ScoreFactor(BaseModel):
    name: str
    delta: int
    detail: str


class IntegrityCheck(BaseModel):
    steps_checked: int
    intact: bool
    broken_at: int | None
    detail: str


class InvestigationDetail(BaseModel):
    investigation_id: str
    mode: str
    llm_model: str | None
    prompt_version: str | None
    decision: str | None
    final_status: str | None
    unexplained_amount: Money | None
    evidence_score: int | None
    #: What the model said about its own certainty. Recorded for analysis and
    #: never used to decide anything - see `evidence_score`.
    reasoning_confidence: int | None
    tool_call_count: int
    latency_ms: int | None
    records_examined: list[str]
    score_factors: list[ScoreFactor]
    steps: list[StepItem]
    evidence: list[EvidenceItem]
    integrity: IntegrityCheck | None


class ExceptionDetail(BaseModel):
    exception: ExceptionSummary
    timeline: list[str]
    investigation: InvestigationDetail | None


class DetectionMetrics(BaseModel):
    injected: int
    detected: int
    true_positives: int
    false_positives: int
    missed: int
    precision_bps: int
    recall_bps: int


class ClassificationMetrics(BaseModel):
    scoreable: int
    correct: int
    incorrect: int
    no_single_correct_type: int
    accuracy_bps: int


class InvestigationMetrics(BaseModel):
    investigated: int
    by_status: dict[str, int]
    mean_tool_calls: float
    mean_evidence_score: float
    #: How far the model's self-assessment sat above the computed score.
    mean_confidence_overclaim: float


class Metrics(BaseModel):
    run_id: str
    records_processed: int
    reconciled_bps: int
    throughput_per_second: float
    batches_out_of_balance: int
    detection: DetectionMetrics
    classification: ClassificationMetrics
    investigation: InvestigationMetrics
    note: str
