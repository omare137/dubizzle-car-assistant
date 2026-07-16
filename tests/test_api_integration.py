"""Live end-to-end API integration tests — real HTTP layer, real DB, REAL LLM.

These cost tokens and need GEMINI_API_KEY, so they are excluded from default
pytest runs (see addopts in pyproject.toml). Run them deliberately with:

    uv run pytest tests/test_api_integration.py -v -m integration
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.agent.long_term_memory import get_profile_summary
from app.db.database import get_connection
from app.db.schema import leads, messages, sessions
from app.main import create_app

pytestmark = pytest.mark.integration

client = TestClient(create_app())


@pytest.fixture
def cleanup_sessions():
    """Collect session ids created via the API; remove their rows afterwards."""
    created: list[str] = []
    yield created
    with get_connection() as conn:
        for sid in created:
            conn.execute(leads.delete().where(leads.c.session_id == sid))
            conn.execute(messages.delete().where(messages.c.session_id == sid))
            conn.execute(sessions.delete().where(sessions.c.id == sid))


def test_session_known_username(cleanup_sessions):
    resp = client.post("/session", json={"username": "omar.k"})
    assert resp.status_code == 200
    data = resp.json()
    cleanup_sessions.append(data["session_id"])
    assert data["user"] == {"id": 1, "name": "Omar Khalid"}
    # returning_user must reflect actual profile presence for this user.
    expected_summary = get_profile_summary(1)
    assert data["returning_user"] is (expected_summary is not None)
    assert data["profile_summary"] == expected_summary


def test_session_unknown_username_404():
    resp = client.post("/session", json={"username": "does.not.exist"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Unknown username"}


def test_chat_valid_session_inventory_question(cleanup_sessions):
    sid = client.post("/session", json={}).json()["session_id"]
    cleanup_sessions.append(sid)
    resp = client.post(
        "/chat", json={"session_id": sid, "message": "What BMWs do you have under 200k AED?"}
    )
    assert resp.status_code == 200
    data = resp.json()
    print("\n----- FULL /chat RESPONSE BODY -----")
    import json as _json

    print(_json.dumps(data, indent=2, ensure_ascii=False))
    assert data["session_id"] == sid
    assert isinstance(data["reply"], str) and data["reply"].strip()
    assert isinstance(data["cars"], list)


def test_chat_bogus_session_404():
    resp = client.post("/chat", json={"session_id": "bogus-id", "message": "hello"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Session not found"}


def test_end_session_sets_ended_at(cleanup_sessions):
    sid = client.post("/session", json={}).json()["session_id"]
    cleanup_sessions.append(sid)
    resp = client.post(f"/session/{sid}/end")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": sid, "ended": True}
    with get_connection() as conn:
        ended_at = conn.execute(
            sa.select(sessions.c.ended_at).where(sessions.c.id == sid)
        ).scalar_one()
    assert ended_at is not None

    assert client.post("/session/bogus-id/end").status_code == 404


def test_full_guest_flow_shape_valid(cleanup_sessions):
    session = client.post("/session", json={}).json()
    cleanup_sessions.append(session["session_id"])
    assert session["user"] is None and session["returning_user"] is False

    resp = client.post(
        "/chat",
        json={"session_id": session["session_id"], "message": "Show me Toyota SUVs"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"session_id", "reply", "cars", "booking_prompt"}
    card_fields = {
        "id": int, "make": str, "model": str, "year": int, "title": str,
        "price_unlisted": bool, "photo_url": str,
    }
    for car in data["cars"]:
        assert set(car) == set(card_fields) | {"price_aed_cash"}
        for field, typ in card_fields.items():
            assert isinstance(car[field], typ), f"{field} wrong type in {car}"
        assert car["price_aed_cash"] is None or isinstance(car["price_aed_cash"], int)
        assert car["price_unlisted"] is (car["price_aed_cash"] is None)
