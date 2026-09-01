"""G16 - the HTTP API.

The API owns no financial logic: it reads back what the engine, the classifier,
the investigator and the audit trail already produced. So these tests check two
things - that it serves those figures faithfully, and that it does not widen
access to anything just because the request arrived over HTTP.

The most important test here is the ground-truth one. Aggregate accuracy is a
legitimate product surface; a per-case answer key served over HTTP is not, and
publishing one would make every accuracy figure this project reports
meaningless.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.api.app import app


@pytest.fixture(scope="module")
def client():
    from backend.db.session import owner_engine

    try:
        with owner_engine().connect() as conn:
            if conn.execute(sa.text("SELECT count(*) FROM ops.payments")).scalar() == 0:
                pytest.skip("no dataset loaded; run scripts/generate_data.py")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL unavailable ({exc})")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def an_exception(client) -> dict:
    return client.get("/api/exceptions", params={"limit": 1}).json()["items"][0]


@pytest.fixture(scope="module")
def an_investigated_exception(client) -> str | None:
    from backend.db.session import owner_engine

    with owner_engine().connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT exception_id FROM recon.investigations ORDER BY started_at DESC LIMIT 1"
            )
        ).scalar()


# --------------------------------------------------------------------------
# The line that must not move
# --------------------------------------------------------------------------


@pytest.mark.db
def test_no_endpoint_leaks_per_case_ground_truth(client, an_investigated_exception):
    """Aggregates yes; an answer key no.

    If a route returned the injected cause for a payment, the agent could read
    it and every accuracy number this project reports would be fiction.
    """
    if not an_investigated_exception:
        pytest.skip("no investigations stored")
    from backend.db.session import eval_engine

    with eval_engine().connect() as conn:
        payment_id, reason, notes = conn.execute(
            sa.text(
                "SELECT t.payment_id, t.reason_code, t.notes FROM gt.case_truth t"
                "  JOIN recon.exceptions e USING (payment_id)"
                " WHERE t.is_exception AND e.exception_id = :e LIMIT 1"
            ),
            {"e": an_investigated_exception},
        ).one_or_none() or (None, None, None)
    if payment_id is None:
        pytest.skip("that exception has no ground-truth row")

    bodies = [
        client.get(f"/api/exceptions/{an_investigated_exception}").text,
        client.get("/api/exceptions", params={"limit": 100}).text,
        client.get("/api/metrics").text,
        client.get("/api/runs/latest").text,
        client.get("/api/health").text,
    ]
    for body in bodies:
        assert notes is None or notes not in body
        # The reason code is the injected cause. A classification the engine
        # arrived at independently may coincide with it, so the check is that
        # the *ground-truth wording* never appears.
        assert "case_truth" not in body
        assert "is_exception" not in body
        assert "injection_params" not in body


@pytest.mark.db
def test_metrics_returns_counts_not_identifiers(client):
    payload = client.get("/api/metrics").json()
    for section in ("detection", "classification", "investigation"):
        for value in payload[section].values():
            assert not isinstance(value, str), f"{section} leaked a string"


def test_no_route_exposes_the_ground_truth_schema():
    paths = {r.path for r in app.routes if getattr(r, "methods", None)}
    assert not any("truth" in p or "/gt" in p for p in paths)


# --------------------------------------------------------------------------
# Health and runs
# --------------------------------------------------------------------------


@pytest.mark.db
def test_health_reports_the_dataset(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["database"] is True
    assert payload["dataset_loaded"] is True
    assert payload["payments"] > 0


@pytest.mark.db
def test_latest_run_matches_the_stored_totals(client):
    from backend.db.session import owner_engine

    payload = client.get("/api/runs/latest").json()
    with owner_engine().connect() as conn:
        row = conn.execute(
            sa.text("SELECT * FROM recon.recon_runs WHERE run_id = :r"),
            {"r": payload["run_id"]},
        ).mappings().one()
    assert payload["records_processed"] == row["records_processed"]
    assert payload["matched"] == row["matched_count"]
    assert payload["exceptions"] == row["exception_count"]
    assert payload["batches_out_of_balance"] == row["batches_out_of_balance"]


@pytest.mark.db
def test_match_rate_counts_pending_as_reconciled(client):
    """Money still inside its settlement window is not an exception."""
    payload = client.get("/api/runs/latest").json()
    expected = (
        (payload["matched"] + payload["pending"]) * 10_000
        // payload["records_processed"]
    )
    assert payload["match_rate_bps"] == expected


# --------------------------------------------------------------------------
# The queue
# --------------------------------------------------------------------------


@pytest.mark.db
def test_the_queue_paginates(client):
    first = client.get("/api/exceptions", params={"limit": 5}).json()
    second = client.get("/api/exceptions", params={"limit": 5, "offset": 5}).json()
    assert len(first["items"]) == 5
    assert first["total"] == second["total"] > 5
    assert {i["exception_id"] for i in first["items"]}.isdisjoint(
        {i["exception_id"] for i in second["items"]}
    )


@pytest.mark.db
@pytest.mark.parametrize(
    "params,check",
    [
        ({"detected_by": "RULE"}, lambda i: i["detected_by"] == "RULE"),
        ({"detected_by": "RESIDUAL"}, lambda i: i["detected_by"] == "RESIDUAL"),
        ({"exception_type": ["FEE_MISMATCH"]},
         lambda i: i["exception_type"] == "FEE_MISMATCH"),
        ({"min_abs_delta": 1_000_00},
         lambda i: abs(i["delta"]["paise"]) >= 1_000_00),
    ],
)
def test_queue_filters_are_applied(client, params, check):
    items = client.get("/api/exceptions", params={**params, "limit": 20}).json()["items"]
    assert items, f"no results for {params}"
    assert all(check(i) for i in items)


@pytest.mark.db
def test_the_queue_leads_with_the_largest_discrepancies(client):
    items = client.get("/api/exceptions", params={"limit": 20}).json()["items"]
    deltas = [abs(i["delta"]["paise"]) for i in items]
    assert deltas == sorted(deltas, reverse=True)


@pytest.mark.db
def test_an_unknown_exception_is_a_404(client):
    assert client.get("/api/exceptions/EX-NOPE").status_code == 404


# --------------------------------------------------------------------------
# Detail and audit
# --------------------------------------------------------------------------


@pytest.mark.db
def test_detail_reassembles_the_whole_investigation(client, an_investigated_exception):
    if not an_investigated_exception:
        pytest.skip("no investigations stored")
    payload = client.get(f"/api/exceptions/{an_investigated_exception}").json()
    investigation = payload["investigation"]
    assert investigation is not None
    assert investigation["mode"] == "AI"
    assert investigation["steps"], "the timeline needs its steps"
    assert investigation["records_examined"]
    assert investigation["score_factors"], "the score must be explainable"
    assert payload["timeline"][0].startswith("DETECTED")
    assert payload["timeline"][-1].startswith("CONCLUDED")


@pytest.mark.db
def test_detail_reports_the_audit_chain_integrity(client, an_investigated_exception):
    if not an_investigated_exception:
        pytest.skip("no investigations stored")
    integrity = client.get(
        f"/api/exceptions/{an_investigated_exception}"
    ).json()["investigation"]["integrity"]
    assert integrity["intact"] is True
    assert integrity["steps_checked"] > 0


@pytest.mark.db
def test_detail_surfaces_the_model_claim_without_acting_on_it(
    client, an_investigated_exception
):
    """Both numbers are shown so a reviewer can see the gap between them."""
    if not an_investigated_exception:
        pytest.skip("no investigations stored")
    investigation = client.get(
        f"/api/exceptions/{an_investigated_exception}"
    ).json()["investigation"]
    assert "reasoning_confidence" in investigation
    assert "evidence_score" in investigation


@pytest.mark.db
def test_rejected_evidence_is_visible_not_hidden(client):
    """A reviewer must see what the model claimed and why it was thrown out."""
    from backend.db.session import owner_engine

    with owner_engine().connect() as conn:
        exception_id = conn.execute(
            sa.text(
                "SELECT e.exception_id FROM recon.evidence v"
                "  JOIN recon.investigations i USING (investigation_id)"
                "  JOIN recon.exceptions e USING (exception_id)"
                " WHERE v.role = 'CONTRADICTS' LIMIT 1"
            )
        ).scalar()
    if not exception_id:
        pytest.skip("no rejected evidence in this dataset")
    evidence = client.get(f"/api/exceptions/{exception_id}").json()["investigation"]["evidence"]
    assert any(e["role"] == "CONTRADICTS" and "REJECTED" in (e["note"] or "")
               for e in evidence)


# --------------------------------------------------------------------------
# Money over the wire
# --------------------------------------------------------------------------


@pytest.mark.db
def test_money_crosses_the_wire_as_integer_paise_and_a_display_string(client):
    """A float here would reintroduce, at the last step, exactly the rounding
    problem the whole financial model is built to avoid."""
    item = client.get("/api/exceptions", params={"limit": 1}).json()["items"][0]
    for field in ("expected_net", "actual_net", "delta"):
        money = item[field]
        assert isinstance(money["paise"], int)
        assert not isinstance(money["paise"], bool)
        assert money["display"].lstrip("-").startswith("₹")


# --------------------------------------------------------------------------
# Investigation endpoint
# --------------------------------------------------------------------------


@pytest.mark.db
def test_investigating_an_unknown_exception_is_a_404(client):
    assert client.post("/api/exceptions/EX-NOPE/investigate").status_code == 404


@pytest.mark.db
def test_an_already_investigated_case_returns_the_stored_result(
    client, an_investigated_exception
):
    """Re-running would spend API quota and contradict the audit trail the
    system already committed to."""
    if not an_investigated_exception:
        pytest.skip("no investigations stored")
    before = client.get(f"/api/exceptions/{an_investigated_exception}").json()
    after = client.post(
        f"/api/exceptions/{an_investigated_exception}/investigate"
    ).json()
    assert after["investigation"]["investigation_id"] == (
        before["investigation"]["investigation_id"]
    )


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_the_openapi_contract_covers_every_route():
    from fastapi.openapi.utils import get_openapi

    spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
    documented = set(spec["paths"])
    assert "/api/health" in documented
    assert "/api/runs/latest" in documented
    assert "/api/exceptions" in documented
    assert "/api/exceptions/{exception_id}" in documented
    assert "/api/exceptions/{exception_id}/investigate" in documented
    assert "/api/metrics" in documented


def test_financial_records_are_never_writable_over_http():
    """The API is an investigation surface, not a way to edit money."""
    mutating = [
        (r.path, sorted(r.methods - {"HEAD", "OPTIONS"}))
        for r in app.routes
        if getattr(r, "methods", None) and (r.methods - {"GET", "HEAD", "OPTIONS"})
    ]
    assert mutating == [("/api/exceptions/{exception_id}/investigate", ["POST"])]
