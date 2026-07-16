"""Tests for app.rag.cross_reference — deterministic, no LLM calls."""

import pytest
import sqlalchemy as sa

from app.db.database import engine, get_connection
from app.db.schema import extraction_disagreements, metadata
from app.rag.cross_reference import log_disagreements


@pytest.fixture(autouse=True)
def clean_test_rows():
    metadata.create_all(engine, tables=[extraction_disagreements])
    yield
    with get_connection() as conn:
        conn.execute(
            extraction_disagreements.delete().where(
                extraction_disagreements.c.user_query.like("test-%")
            )
        )


def agent_car(car_id: int, price: int | None, body_type: str = "suv") -> dict:
    return {"id": car_id, "price": price, "body_type": body_type}


def fetch_rows(query: str) -> list[dict]:
    with get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                sa.select(extraction_disagreements).where(
                    extraction_disagreements.c.user_query == query
                )
            ).mappings()
        ]


def test_agent_price_where_deterministic_null_is_logged():
    # Car 38 (Bugatti Chiron): deterministic cash AND monthly are both null.
    written = log_disagreements(
        [agent_car(38, price=50_000, body_type="sports_car")],
        session_id=None,
        user_query="test-null-vs-price",
    )
    assert written == 1
    rows = fetch_rows("test-null-vs-price")
    assert len(rows) == 1
    row = rows[0]
    assert row["car_id"] == 38
    assert row["deterministic_price_cash"] is None
    assert row["deterministic_price_monthly"] is None
    assert row["deterministic_body_type"] is None  # pending — no det column yet
    assert row["agent_price"] == 50_000
    assert row["agent_body_type"] == "sports_car"
    assert row["session_id"] is None


def test_agreement_writes_nothing():
    # Car 5 (Haval H9): deterministic cash = 115,750. Agent agrees exactly.
    written = log_disagreements(
        [agent_car(5, price=115_750)], user_query="test-agreement"
    )
    assert written == 0
    assert fetch_rows("test-agreement") == []
    # Both-null is also agreement (car 38 with agent null).
    assert log_disagreements([agent_car(38, price=None)], user_query="test-agreement") == 0


def test_agent_null_where_deterministic_has_price_is_logged():
    written = log_disagreements([agent_car(5, price=None)], user_query="test-missed-price")
    assert written == 1
    row = fetch_rows("test-missed-price")[0]
    assert row["deterministic_price_cash"] == 115_750
    assert row["agent_price"] is None


def test_price_within_tolerance_agrees_and_monthly_counts():
    # Within 1% of deterministic cash → agree.
    assert log_disagreements([agent_car(5, price=115_000)], user_query="test-tol") == 0
    # Car 6 (C200): cash 106,000 / monthly 1,611 — agent matching monthly agrees.
    assert log_disagreements([agent_car(6, price=1_611)], user_query="test-tol") == 0
    assert fetch_rows("test-tol") == []
