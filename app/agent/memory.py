"""Short-term (within-session) memory: persistence around run_agent_turn.

The `messages` table stores only the human-readable transcript (user +
assistant text). Tool-call mechanics stay in-memory within a single turn —
we don't replay tool history across turns.
"""

import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from app.agent.orchestrator import run_agent_turn
from app.agent.tools_impl import TOOL_CONTEXT
from app.db.database import get_connection
from app.db.schema import messages, sessions, users, utcnow


def create_session(user_id: int | None) -> str:
    session_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(sessions.insert().values(id=session_id, user_id=user_id))
    return session_id


def load_session_messages(session_id: str) -> list[dict]:
    """Transcript for a session in insertion order, ready for run_agent_turn.
    [] for unknown sessions or sessions with no messages yet."""
    query = (
        sa.select(messages.c.role, messages.c.content)
        .where(messages.c.session_id == session_id)
        .order_by(messages.c.id)
    )
    with get_connection() as conn:
        return [{"role": r, "content": c} for r, c in conn.execute(query)]


def append_message(session_id: str, role: str, content: str) -> None:
    with get_connection() as conn:
        conn.execute(messages.insert().values(session_id=session_id, role=role, content=content))


def session_exists(session_id: str) -> bool:
    with get_connection() as conn:
        return (
            conn.execute(
                sa.select(sa.literal(1)).where(sessions.c.id == session_id).limit(1)
            ).scalar_one_or_none()
            is not None
        )


def get_user_by_username(username: str) -> dict | None:
    query = sa.select(users.c.id, users.c.name, users.c.username).where(
        users.c.username == username
    )
    with get_connection() as conn:
        row = conn.execute(query).mappings().one_or_none()
    return dict(row) if row else None


def get_session_user(session_id: str) -> dict | None:
    """The session's logged-in user, or None for guest/unknown sessions."""
    query = (
        sa.select(users.c.id, users.c.name, users.c.username)
        .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
        .where(sessions.c.id == session_id)
    )
    with get_connection() as conn:
        row = conn.execute(query).mappings().one_or_none()
    return dict(row) if row else None


def run_turn_with_memory(
    session_id: str,
    user_message: str,
    profile_summary: str | None,
    on_tool_call: Callable[[str, dict, Any], None] | None = None,
) -> dict[str, Any]:
    """Load history, run one agent turn, persist both sides of the exchange.

    Injects the real session_id/user_id into the tool layer via TOOL_CONTEXT,
    so save_lead/confirm_booking never depend on the model knowing real ids
    (the unreliable path that broke in Phase 6).
    """
    history = load_session_messages(session_id)
    append_message(session_id, "user", user_message)
    turn_messages = history + [{"role": "user", "content": user_message}]

    user = get_session_user(session_id)
    token = TOOL_CONTEXT.set(
        {"session_id": session_id, "user_id": user["id"] if user else None}
    )
    try:
        result = run_agent_turn(
            messages=turn_messages,
            profile_summary=profile_summary,
            on_tool_call=on_tool_call,
        )
    finally:
        TOOL_CONTEXT.reset(token)

    append_message(session_id, "assistant", result["reply"])
    return result


def end_session(session_id: str) -> dict | None:
    """Explicitly end a session: stamp ended_at and compile the transcript
    into the user's long-term profile. Called by POST /session/{id}/end —
    deliberately NOT run after every message. Returns the compiled profile
    (None for guests/empty sessions)."""
    # Local import: long_term_memory imports from this module.
    from app.agent.long_term_memory import compile_session_to_profile

    with get_connection() as conn:
        conn.execute(
            sessions.update().where(sessions.c.id == session_id).values(ended_at=utcnow())
        )
    return compile_session_to_profile(session_id)
