"""Direct tests for the agent tool functions — no LLM calls."""

import uuid

import pytest
import sqlalchemy as sa

from app.agent.tools_impl import (
    TURN_SEEN_CAR_IDS,
    check_availability,
    confirm_booking,
    get_car_details,
    propose_booking,
    save_lead,
    search_inventory,
)
from app.db.database import get_connection
from app.db.schema import leads, sessions


@pytest.fixture
def session_id():
    """A real sessions row (leads.session_id FK is enforced), cleaned up after."""
    sid = f"test-{uuid.uuid4()}"
    with get_connection() as conn:
        conn.execute(sessions.insert().values(id=sid))
    yield sid
    with get_connection() as conn:
        conn.execute(leads.delete().where(leads.c.session_id == sid))
        conn.execute(sessions.delete().where(sessions.c.id == sid))


def test_search_tool_ors_toyota_and_tova():
    makes = {r["make"] for r in search_inventory(make="toyota", limit=100)}
    assert makes == {"toyota", "tova"}
    # The alias only kicks in for toyota queries, not others.
    assert {r["make"] for r in search_inventory(make="bmw", limit=100)} == {"bmw"}


def test_check_availability_rejects_sunday():
    result = check_availability(car_id=1, day="Sunday", time="14:00")
    assert result["available"] is False
    assert "Monday to Saturday" in result["reason"]


def test_check_availability_rejects_after_hours():
    result = check_availability(car_id=1, day="Wednesday", time="21:00")
    assert result["available"] is False
    assert "opening hours" in result["reason"]


def test_check_availability_accepts_wednesday_afternoon():
    result = check_availability(car_id=1, day="Wednesday", time="14:00")
    assert result == {"available": True, "car_id": 1, "day": "Wednesday", "time": "14:00"}


def test_propose_booking_returns_structured_payload():
    result = propose_booking(car_id=1, day="wednesday", time="14:00")
    assert result["status"] == "proposed"
    assert result["booking_prompt"] == {"car_id": 1, "day": "Wednesday", "time": "14:00"}


def test_propose_booking_rejects_car_id_not_confirmed_this_turn():
    """Structural guard: during an agent turn (TURN_SEEN_CAR_IDS active), a
    car_id the model didn't obtain from a tool call THIS turn is rejected —
    the exact wrong-Ferrari scenario from the live audit."""
    token = TURN_SEEN_CAR_IDS.set(set())  # turn started, no cars fetched yet
    try:
        result = propose_booking(car_id=1, day="Wednesday", time="14:00")
        assert "error" in result
        assert "not returned by search_inventory or get_car_details" in result["error"]
        assert "booking_prompt" not in result

        # After a real get_car_details in the same turn, the same id is allowed.
        assert get_car_details(1) is not None
        result = propose_booking(car_id=1, day="Wednesday", time="14:00")
        assert result["status"] == "proposed"
        assert result["booking_prompt"]["car_id"] == 1

        # search_inventory results count as confirmed too.
        ids = {r["id"] for r in search_inventory(make="audi", model="q8")}
        assert all(propose_booking(i, "Friday", "11:00")["status"] == "proposed" for i in ids)
    finally:
        TURN_SEEN_CAR_IDS.reset(token)
    # Outside an agent turn (no context), direct calls stay trusted.
    assert propose_booking(car_id=1, day="Wednesday", time="14:00")["status"] == "proposed"


def test_check_availability_does_not_launder_unconfirmed_ids():
    """Passing a guessed id through check_availability must not mark it as
    turn-confirmed for propose_booking."""
    token = TURN_SEEN_CAR_IDS.set(set())
    try:
        assert check_availability(car_id=1, day="Wednesday", time="14:00")["available"] is True
        result = propose_booking(car_id=1, day="Wednesday", time="14:00")
        assert "error" in result
    finally:
        TURN_SEEN_CAR_IDS.reset(token)


def test_propose_booking_invalid_slot_writes_nothing(session_id):
    result = propose_booking(car_id=1, day="Sunday", time="14:00")
    assert result["status"] == "invalid"
    with get_connection() as conn:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(leads).where(leads.c.session_id == session_id)
        ).scalar_one()
    assert count == 0


def test_confirm_booking_writes_confirmed_lead(session_id):
    result = confirm_booking(car_id=1, day="Wednesday", time="14:00", session_id=session_id)
    assert result["status"] == "booking_confirmed"
    with get_connection() as conn:
        row = conn.execute(
            sa.select(leads).where(leads.c.id == result["lead_id"])
        ).mappings().one()
    assert row["status"] == "booking_confirmed"
    assert row["car_id"] == 1
    assert row["booking_day"] == "Wednesday"
    assert row["booking_time"] == "14:00"
    assert row["session_id"] == session_id


def test_save_lead_writes_captured_lead(session_id):
    result = save_lead(
        session_id=session_id,
        budget_range="under 200,000 AED",
        needs_notes="family SUV, low mileage",
    )
    assert result["status"] == "captured"
    with get_connection() as conn:
        row = conn.execute(sa.select(leads).where(leads.c.id == result["lead_id"])).mappings().one()
    assert row["status"] == "captured"
    assert row["budget_range"] == "under 200,000 AED"
    assert row["needs_notes"] == "family SUV, low mileage"
    assert row["car_id"] is None  # general capture, no car picked


def test_save_lead_updates_existing_captured_lead(session_id):
    first = save_lead(session_id=session_id, budget_range="under 200k AED")
    second = save_lead(session_id=session_id, car_id=6, needs_notes="wants the C200")
    assert second["lead_id"] == first["lead_id"]
    assert second["updated"] is True
    with get_connection() as conn:
        row = conn.execute(sa.select(leads).where(leads.c.id == first["lead_id"])).mappings().one()
    # Update merges: earlier budget survives, new fields added.
    assert row["budget_range"] == "under 200k AED"
    assert row["car_id"] == 6
    assert row["needs_notes"] == "wants the C200"
