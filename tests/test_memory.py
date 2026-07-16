"""Tests for app.agent.memory — run_agent_turn is mocked, no LLM calls."""

import pytest
import sqlalchemy as sa

from app.agent import memory
from app.agent.tools_impl import save_lead
from app.db.database import get_connection
from app.db.schema import leads, messages, sessions


@pytest.fixture
def track_sessions():
    """Collect session ids created during a test and clean up their rows."""
    created: list[str] = []
    yield created
    with get_connection() as conn:
        for sid in created:
            conn.execute(leads.delete().where(leads.c.session_id == sid))
            conn.execute(messages.delete().where(messages.c.session_id == sid))
            conn.execute(sessions.delete().where(sessions.c.id == sid))


def test_create_session_inserts_row_and_returns_usable_id(track_sessions):
    sid = memory.create_session(user_id=None)
    track_sessions.append(sid)
    with get_connection() as conn:
        row = conn.execute(sa.select(sessions).where(sessions.c.id == sid)).mappings().one()
    assert row["user_id"] is None
    assert row["started_at"] is not None
    assert memory.load_session_messages(sid) == []


def test_append_and_load_round_trip_preserves_order(track_sessions):
    sid = memory.create_session(user_id=None)
    track_sessions.append(sid)
    memory.append_message(sid, "user", "first")
    memory.append_message(sid, "assistant", "second")
    memory.append_message(sid, "user", "third")
    assert memory.load_session_messages(sid) == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]


def test_load_messages_unknown_session_returns_empty():
    assert memory.load_session_messages("no-such-session") == []


def test_two_turn_conversation_context_available(track_sessions, monkeypatch):
    sid = memory.create_session(user_id=None)
    track_sessions.append(sid)
    seen_messages: list[list[dict]] = []

    def fake_run_agent_turn(messages, profile_summary, on_tool_call=None):
        seen_messages.append(messages)
        return {
            "reply": "The Mercedes GLC is a great pick — brand new, 2-year warranty.",
            "cars": [], "booking_prompt": None, "updated_messages": messages,
        }

    monkeypatch.setattr(memory, "run_agent_turn", fake_run_agent_turn)

    memory.run_turn_with_memory(sid, "Show me Mercedes SUVs", None)
    memory.run_turn_with_memory(sid, "Is there a warranty on it?", None)

    # Turn 2 sees turn 1's full exchange, so "it" is resolvable.
    turn2 = seen_messages[1]
    assert turn2 == [
        {"role": "user", "content": "Show me Mercedes SUVs"},
        {"role": "assistant", "content": "The Mercedes GLC is a great pick — brand new, 2-year warranty."},
        {"role": "user", "content": "Is there a warranty on it?"},
    ]
    # And the transcript is fully persisted (2 turns = 4 rows).
    assert len(memory.load_session_messages(sid)) == 4


def test_get_session_user_guest_and_logged_in(track_sessions):
    guest_sid = memory.create_session(user_id=None)
    track_sessions.append(guest_sid)
    assert memory.get_session_user(guest_sid) is None
    assert memory.get_session_user("no-such-session") is None

    user_sid = memory.create_session(user_id=1)  # seeded omar.k
    track_sessions.append(user_sid)
    assert memory.get_session_user(user_sid) == {
        "id": 1, "name": "Omar Khalid", "username": "omar.k",
    }


def test_session_id_injection_overrides_model_garbage(track_sessions, monkeypatch):
    sid = memory.create_session(user_id=1)
    track_sessions.append(sid)

    def fake_run_agent_turn(messages, profile_summary, on_tool_call=None):
        # Simulate the model calling save_lead with an invented session_id
        # (what actually happened in the Phase 6 smoke test).
        result = save_lead(session_id="chat_session_12345", budget_range="under 200k AED")
        assert "error" not in result, f"tool call failed: {result}"
        return {"reply": "noted!", "cars": [], "booking_prompt": None, "updated_messages": messages}

    monkeypatch.setattr(memory, "run_agent_turn", fake_run_agent_turn)
    memory.run_turn_with_memory(sid, "My budget is under 200k", None)

    with get_connection() as conn:
        row = conn.execute(sa.select(leads).where(leads.c.session_id == sid)).mappings().one()
        garbage = conn.execute(
            sa.select(sa.func.count()).select_from(leads)
            .where(leads.c.session_id == "chat_session_12345")
        ).scalar_one()
    # Real session id used, model's garbage discarded; trusted user_id attached.
    assert row["budget_range"] == "under 200k AED"
    assert row["user_id"] == 1
    assert garbage == 0
