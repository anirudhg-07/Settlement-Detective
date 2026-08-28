"""G14 - the audit trail.

Two guarantees, and they cover different attackers.

**Grants** stop the system that writes the trail from altering it: the agent
role holds INSERT and no UPDATE or DELETE on `investigation_steps`.

**The hash chain** covers everyone else. Each step commits to the one before
it, so a row edited, deleted or reordered by any means stops matching and the
break is locatable.

That is tamper *evidence*, not tamper proofing - someone who rewrites every
hash from the tampered step onward would pass. Defeating that needs the chain
head anchored outside this database, which is a production concern and is not
pretended at here.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.audit.trail import (
    canonical,
    chain_steps,
    extract_record_ids,
    genesis_hash,
    reconstruct,
    step_hash,
    verify_chain,
)


@pytest.fixture
def conn():
    from backend.db.session import owner_engine

    try:
        engine = owner_engine()
        with engine.connect() as probe:
            probe.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL unavailable ({exc})")
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            if connection.execute(
                sa.text("SELECT count(*) FROM recon.investigations")
            ).scalar() == 0:
                pytest.skip("no investigations stored; run scripts/investigate.py")
            yield connection
        finally:
            tx.rollback()


@pytest.fixture
def an_investigation(conn) -> str:
    return conn.execute(
        sa.text(
            "SELECT investigation_id FROM recon.investigations"
            " ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


def test_hashing_is_stable_regardless_of_key_order():
    """A hash must depend on content, not on how a dict happened to serialise."""
    a = {"seq": 1, "tool_name": "get_case_bundle", "tool_args": {"x": 1, "y": 2}}
    b = {"tool_args": {"y": 2, "x": 1}, "tool_name": "get_case_bundle", "seq": 1}
    assert canonical(a) == canonical(b)
    assert step_hash("prev", a) == step_hash("prev", b)


def test_each_step_commits_to_the_one_before_it():
    steps = [
        {"investigation_id": "inv_1", "seq": n, "step_type": "tool_call",
         "tool_name": "t", "tool_args": {}, "tool_result": {}, "observation": None}
        for n in (1, 2, 3)
    ]
    chain_steps("inv_1", steps)
    assert steps[0]["prev_hash"] == genesis_hash("inv_1")
    assert steps[1]["prev_hash"] == steps[0]["content_hash"]
    assert steps[2]["prev_hash"] == steps[1]["content_hash"]


def test_a_chain_cannot_be_lifted_into_another_investigation():
    """The genesis hash ties the chain to its own investigation."""
    assert genesis_hash("inv_1") != genesis_hash("inv_2")


def test_changing_any_field_changes_the_hash():
    base = {"investigation_id": "inv_1", "seq": 1, "step_type": "tool_call",
            "tool_name": "t", "tool_args": {"a": 1}, "tool_result": {"b": 2},
            "observation": "note"}
    original = step_hash("prev", base)
    for field, value in [
        ("tool_name", "other"), ("tool_args", {"a": 2}),
        ("tool_result", {"b": 3}), ("observation", "edited"), ("seq", 2),
    ]:
        assert step_hash("prev", {**base, field: value}) != original


# --------------------------------------------------------------------------
# Tamper detection against the live database
# --------------------------------------------------------------------------


@pytest.mark.db
def test_a_stored_trail_verifies_clean(conn, an_investigation):
    check = verify_chain(conn, an_investigation)
    assert check.intact, check.detail
    assert check.steps_checked > 0


@pytest.mark.db
def test_editing_a_step_is_detected(conn, an_investigation):
    """The agent role cannot do this at all. The owner can - and is seen."""
    conn.execute(
        sa.text(
            "UPDATE recon.investigation_steps SET observation = 'looks fine to me'"
            " WHERE investigation_id = :i AND seq = 2"
        ),
        {"i": an_investigation},
    )
    check = verify_chain(conn, an_investigation)
    assert not check.intact
    assert check.broken_at == 2
    assert "edited after it was written" in check.detail


@pytest.mark.db
def test_rewriting_a_stored_tool_result_is_detected(conn, an_investigation):
    conn.execute(
        sa.text(
            "UPDATE recon.investigation_steps SET tool_result = CAST(:v AS jsonb)"
            " WHERE investigation_id = :i AND seq = 2"
        ),
        {"i": an_investigation, "v": '{"everything":"was fine"}'},
    )
    assert not verify_chain(conn, an_investigation).intact


@pytest.mark.db
def test_deleting_a_step_is_detected(conn, an_investigation):
    conn.execute(
        sa.text(
            "DELETE FROM recon.investigation_steps"
            " WHERE investigation_id = :i AND seq = 2"
        ),
        {"i": an_investigation},
    )
    check = verify_chain(conn, an_investigation)
    assert not check.intact
    assert "inserted, removed or reordered" in check.detail


@pytest.mark.db
def test_an_investigation_with_no_steps_does_not_pass_as_intact(conn):
    check = verify_chain(conn, "inv_doesnotexist")
    assert not check.intact
    assert "no steps" in check.detail


# --------------------------------------------------------------------------
# Reconstruction
# --------------------------------------------------------------------------


@pytest.mark.db
def test_an_investigation_reconstructs_from_the_database_alone(conn):
    exception_id = conn.execute(
        sa.text(
            "SELECT exception_id FROM recon.investigations"
            " ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()
    trail = reconstruct(conn, exception_id)
    assert trail is not None
    assert trail.exception["exception_id"] == exception_id
    assert trail.investigation["final_status"]
    assert trail.steps
    assert trail.chain.intact


@pytest.mark.db
def test_the_score_is_explainable_from_the_record(conn):
    """Storing only the number leaves an auditor unable to answer "why 41?"."""
    exception_id = conn.execute(
        sa.text(
            "SELECT exception_id FROM recon.investigations"
            " ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()
    trail = reconstruct(conn, exception_id)
    factors = trail.investigation["score_factors"]
    assert factors is not None
    assert all({"name", "delta", "detail"} <= set(f) for f in factors)
    lines = trail.why_this_score()
    assert "starting score" in lines[0]
    assert str(trail.investigation["evidence_score"]) in lines[-1]


@pytest.mark.db
def test_records_examined_lists_records_not_field_names(conn):
    """`fee_deducted` is a JSON key, not a record - matching on the prefix
    alone swept those in."""
    exception_id = conn.execute(
        sa.text(
            "SELECT exception_id FROM recon.investigations"
            " ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()
    trail = reconstruct(conn, exception_id)
    assert trail.records_examined
    assert "fee_deducted" not in trail.records_examined
    for record in trail.records_examined:
        prefix, _, suffix = record.partition("_")
        assert len(suffix) == 14, record
        table = {"pay": "ops.payments", "order": "ops.orders", "fee": "ops.fees",
                 "rfnd": "ops.refunds", "adj": "ops.adjustments",
                 "setl": "ops.settlements", "si": "ops.settlement_items",
                 "cust": "ops.customers"}[prefix]
        column = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = :s AND table_name = :t"
                " ORDER BY ordinal_position LIMIT 1"
            ),
            {"s": table.split(".")[0], "t": table.split(".")[1]},
        ).scalar()
        assert conn.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE {column} = :v"), {"v": record}
        ).scalar(), f"{record} is not a real row in {table}"


@pytest.mark.db
def test_the_timeline_reads_as_a_sequence(conn):
    exception_id = conn.execute(
        sa.text(
            "SELECT exception_id FROM recon.investigations"
            " ORDER BY started_at DESC LIMIT 1"
        )
    ).scalar()
    lines = reconstruct(conn, exception_id).timeline()
    assert lines[0].startswith("DETECTED")
    assert any(line.startswith("INVESTIGATING") for line in lines)
    assert lines[-1].startswith("CONCLUDED")


@pytest.mark.db
def test_an_unknown_exception_reconstructs_to_nothing(conn):
    assert reconstruct(conn, "EX-DOESNOTEXIST") is None


@pytest.mark.db
def test_every_stored_trail_is_intact(conn):
    """The check that would catch a corrupted trail in the whole dataset."""
    broken = []
    for (investigation_id,) in conn.execute(
        sa.text("SELECT investigation_id FROM recon.investigations")
    ):
        check = verify_chain(conn, investigation_id)
        if not check.intact:
            broken.append((investigation_id, check.detail))
    assert broken == []


def test_the_extractor_ignores_json_keys():
    payload = {"fee_deducted": 1, "fee_retained": 2,
               "fee": {"record_id": "fee_8YeQpEIn33gjBH"},
               "items": ["pay_WpxjXmAHnkliPd", "not_an_id"]}
    assert extract_record_ids(payload) == {
        "fee_8YeQpEIn33gjBH", "pay_WpxjXmAHnkliPd"
    }
