"""Tests for app.agent.long_term_memory — get_completion is mocked throughout."""

import json
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.agent import long_term_memory as ltm
from app.agent.memory import append_message, create_session
from app.db.database import get_connection
from app.db.schema import messages, profiles, sessions, users, utcnow


def fake_llm_response(content: str):
    """Minimal stand-in for litellm.ModelResponse."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def temp_user():
    """A throwaway user (guaranteed no pre-existing profile), cleaned up after."""
    username = f"test-{uuid.uuid4().hex[:8]}"
    with get_connection() as conn:
        user_id = conn.execute(
            users.insert().values(username=username, name="Test User")
        ).inserted_primary_key[0]
    yield user_id
    with get_connection() as conn:
        for sid_row in conn.execute(sa.select(sessions.c.id).where(sessions.c.user_id == user_id)):
            conn.execute(messages.delete().where(messages.c.session_id == sid_row[0]))
        conn.execute(sessions.delete().where(sessions.c.user_id == user_id))
        conn.execute(profiles.delete().where(profiles.c.user_id == user_id))
        conn.execute(users.delete().where(users.c.id == user_id))


@pytest.fixture
def guest_session():
    sid = create_session(user_id=None)
    yield sid
    with get_connection() as conn:
        conn.execute(messages.delete().where(messages.c.session_id == sid))
        conn.execute(sessions.delete().where(sessions.c.id == sid))


def seed_transcript(session_id: str):
    append_message(session_id, "user", "I want a Toyota SUV under 150k AED")
    append_message(session_id, "assistant", "The 2025 Toyota BZ4X could fit — brand new, GCC specs.")


def test_guest_session_returns_none_and_calls_no_llm(guest_session, monkeypatch):
    seed_transcript(guest_session)
    monkeypatch.setattr(ltm, "get_completion", lambda *a, **k: pytest.fail("LLM called for guest"))
    assert ltm.compile_session_to_profile(guest_session) is None


def test_empty_transcript_returns_none(temp_user, monkeypatch):
    sid = create_session(user_id=temp_user)
    monkeypatch.setattr(ltm, "get_completion", lambda *a, **k: pytest.fail("LLM called for empty"))
    assert ltm.compile_session_to_profile(sid) is None
    # A transcript with no assistant replies is also 'nothing to compile'.
    append_message(sid, "user", "hello?")
    assert ltm.compile_session_to_profile(sid) is None


def test_compile_merges_with_existing_profile(temp_user, monkeypatch):
    with get_connection() as conn:
        conn.execute(
            profiles.insert().values(
                user_id=temp_user,
                preferences_json=json.dumps(
                    {"budget_range": "under 100k AED", "preferred_makes": ["toyota"],
                     "preferred_body_type": None, "cars_of_interest": [31], "notes": "wants GCC specs"}
                ),
                last_updated=utcnow(),
            )
        )
    sid = create_session(user_id=temp_user)
    seed_transcript(sid)
    monkeypatch.setattr(
        ltm, "get_completion",
        lambda *a, **k: fake_llm_response(json.dumps(
            {"budget_range": "under 150,000 AED", "preferred_makes": ["Nissan"],
             "preferred_body_type": "SUV", "cars_of_interest": [], "notes": "wants GCC specs"}
        )),
    )
    merged = ltm.compile_session_to_profile(sid)

    assert sorted(merged["preferred_makes"]) == ["nissan", "toyota"]  # union, deduped, normalized
    assert merged["budget_range"] == "under 150,000 AED"  # new wins
    assert merged["preferred_body_type"] == "SUV"  # new fills old null
    assert merged["cars_of_interest"] == [31]  # old survives
    assert merged["notes"] == "wants GCC specs"  # identical note not duplicated
    # And it was persisted.
    assert ltm._load_profile(temp_user) == merged


def test_car_mentions_grounded_to_real_ids_and_inventions_dropped(temp_user, monkeypatch):
    sid = create_session(user_id=temp_user)
    seed_transcript(sid)
    monkeypatch.setattr(
        ltm, "get_completion",
        lambda *a, **k: fake_llm_response(json.dumps(
            {"budget_range": None, "preferred_makes": [], "preferred_body_type": None,
             "cars_of_interest": ["2025 Toyota BZ4X", "1962 Batmobile Special"], "notes": None}
        )),
    )
    merged = ltm.compile_session_to_profile(sid)
    assert len(merged["cars_of_interest"]) == 1  # BZ4X resolved, Batmobile dropped
    with get_connection() as conn:
        title = conn.execute(
            sa.select(ltm.inventory.c.title).where(ltm.inventory.c.id == merged["cars_of_interest"][0])
        ).scalar_one()
    assert "BZ4X" in title


def test_malformed_llm_json_returns_none_without_crashing(temp_user, monkeypatch):
    sid = create_session(user_id=temp_user)
    seed_transcript(sid)
    monkeypatch.setattr(
        ltm, "get_completion", lambda *a, **k: fake_llm_response("Sure! The user wants {a car")
    )
    assert ltm.compile_session_to_profile(sid) is None
    assert ltm._load_profile(temp_user) is None  # nothing written


def test_markdown_fenced_json_is_parsed(temp_user, monkeypatch):
    sid = create_session(user_id=temp_user)
    seed_transcript(sid)
    fenced = '```json\n{"budget_range": "under 150k AED", "preferred_makes": [], ' \
             '"preferred_body_type": null, "cars_of_interest": [], "notes": null}\n```'
    monkeypatch.setattr(ltm, "get_completion", lambda *a, **k: fake_llm_response(fenced))
    merged = ltm.compile_session_to_profile(sid)
    assert merged["budget_range"] == "under 150k AED"


def test_get_profile_summary_none_when_no_profile(temp_user):
    assert ltm.get_profile_summary(temp_user) is None


def test_get_profile_summary_formats_populated_profile(temp_user):
    with get_connection() as conn:
        conn.execute(
            profiles.insert().values(
                user_id=temp_user,
                preferences_json=json.dumps(
                    {"budget_range": "under 150,000 AED", "preferred_makes": ["nissan", "toyota"],
                     "preferred_body_type": "SUV", "cars_of_interest": [31],
                     "notes": "prefers GCC specs"}
                ),
                last_updated=utcnow(),
            )
        )
    summary = ltm.get_profile_summary(temp_user)
    for fact in ["suv", "toyota", "nissan", "under 150,000 aed", "bz4x", "gcc specs"]:
        assert fact in summary.lower(), f"{fact!r} missing from: {summary}"
    assert summary.startswith("Previously interested in")
