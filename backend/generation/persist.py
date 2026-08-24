"""Bulk-load a generated ``World`` into PostgreSQL.

Insert order follows the foreign keys: settlements before the adjustments that
reference them, adjustments and refunds before the settlement lines that
reference them. Ground truth is written last, into schema ``gt``, which the
agent role cannot read.
"""

from __future__ import annotations

from contextlib import nullcontext

from sqlalchemy import Connection, Engine, text

from backend.generation.generator import World
from backend.models import (
    Adjustment,
    CaseTruth,
    Customer,
    Fee,
    Order,
    Payment,
    Refund,
    Settlement,
    SettlementItem,
)

CHUNK_SIZE = 2_000

#: Dependency-ordered: each table's foreign keys point only at earlier entries.
LOAD_ORDER = (
    ("customers", Customer, "customers"),
    ("orders", Order, "orders"),
    ("payments", Payment, "payments"),
    ("fees", Fee, "fees"),
    ("refunds", Refund, "refunds"),
    ("settlements", Settlement, "settlements"),
    ("adjustments", Adjustment, "adjustments"),
    ("settlement_items", SettlementItem, "settlement_items"),
    ("truths", CaseTruth, "gt.case_truth"),
)

#: Truncated as one statement so foreign keys never block the order.
TRUNCATE_SQL = """
TRUNCATE TABLE
    gt.case_truth,
    ops.settlement_items,
    ops.adjustments,
    ops.settlements,
    ops.refunds,
    ops.fees,
    ops.payments,
    ops.orders,
    ops.customers
RESTART IDENTITY CASCADE
"""


def truncate_all(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(TRUNCATE_SQL))


def persist(
    world: World,
    target: Engine | Connection,
    *,
    truncate: bool = True,
) -> dict[str, int]:
    """Write the world. Returns rows inserted per table.

    Accepts an ``Engine`` (commits) or an existing ``Connection`` (leaves the
    caller's transaction open, so a test can roll the whole load back rather
    than destroying a loaded dataset).
    """
    written: dict[str, int] = {}
    is_engine = isinstance(target, Engine)
    ctx = target.begin() if is_engine else nullcontext(target)
    with ctx as conn:
        if truncate:
            conn.execute(text(TRUNCATE_SQL))
        for attr, model, label in LOAD_ORDER:
            rows = getattr(world, attr)
            for start in range(0, len(rows), CHUNK_SIZE):
                chunk = rows[start : start + CHUNK_SIZE]
                if chunk:
                    conn.execute(model.__table__.insert(), chunk)
            written[label] = len(rows)
    return written
