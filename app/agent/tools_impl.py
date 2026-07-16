"""Implementations of the agent's tools + the dispatch table.

Every function returns a JSON-serializable dict/list. Invalid input returns a
structured {"available"/"status": ..., "reason": ...} style payload rather than
raising, so the LLM can read the failure and correct course.
"""

import re
from contextvars import ContextVar
from typing import Any

import sqlalchemy as sa

from app.db.database import get_connection
from app.db.schema import leads
from app.tools import inventory_search as inv

# Trusted per-turn identity, set by the memory layer (run_turn_with_memory)
# around each agent turn. When set, save_lead/confirm_booking take session_id
# and user_id from here, OVERRIDING whatever the model passed — the LLM cannot
# be trusted to know real ids (in Phase 6 it invented one and hit an FK error).
TOOL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("TOOL_CONTEXT", default=None)

# Car ids actually returned by search_inventory/get_car_details in the CURRENT
# turn. Reset by the orchestrator at the start of each turn. propose_booking
# rejects ids outside this set — a structural guard against the model guessing
# a car_id on follow-up turns (observed live: it proposed an undiscussed
# Ferrari after inventing an id). None = no agent turn active (direct
# programmatic calls are trusted, same convention as TOOL_CONTEXT).
TURN_SEEN_CAR_IDS: ContextVar[set[int] | None] = ContextVar("TURN_SEEN_CAR_IDS", default=None)


def _mark_seen(car_ids: list[int]) -> None:
    seen = TURN_SEEN_CAR_IDS.get()
    if seen is not None:
        seen.update(car_ids)


def _trusted_identity(session_id: str, user_id: int | None) -> tuple[str, int | None]:
    ctx = TOOL_CONTEXT.get()
    if ctx is None:  # direct/programmatic call — trust the caller's args
        return session_id, user_id
    return ctx["session_id"], ctx.get("user_id")

# The catalog contains 'tova' rows that are a typo variant of 'toyota' in the
# source data (found in Phase 3 via list_distinct_makes). We normalize at the
# tool layer — not in the DB — so the stored data stays a faithful copy of the
# raw listings and the alias policy lives in one greppable place.
MAKE_ALIASES: dict[str, list[str]] = {
    "toyota": ["toyota", "tova"],
}

VALID_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
OPENING_TIME = "08:00"
CLOSING_TIME = "20:00"


def search_inventory(
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    keywords: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    makes = MAKE_ALIASES.get(make.strip().lower(), [make]) if make else [None]
    seen: dict[int, dict[str, Any]] = {}
    for alias in makes:
        for row in inv.search_inventory(
            make=alias, model=model, year_min=year_min, year_max=year_max,
            price_max=price_max, keywords=keywords, limit=limit,
        ):
            seen.setdefault(row["id"], row)
    results = sorted(seen.values(), key=lambda r: r["id"])[:limit]
    _mark_seen([r["id"] for r in results])
    return results


def get_car_details(car_id: int) -> dict[str, Any] | None:
    row = inv.get_car_details(car_id)
    if row is not None:
        _mark_seen([row["id"]])
    return row


def check_availability(car_id: int, day: str, time: str) -> dict[str, Any]:
    """Validate a viewing slot: Monday-Saturday, 08:00-20:00.

    Intentionally simple — a real system would check an actual slot calendar
    (and per-showroom hours); for this phase any in-hours weekday slot is free.
    """
    day_norm = day.strip().lower()
    matched_day = next(
        (d for d in VALID_DAYS if d == day_norm or (len(day_norm) >= 3 and d.startswith(day_norm))),
        None,
    )
    if matched_day is None:
        return {
            "available": False,
            "reason": f"Viewings run Monday to Saturday only — '{day}' is not bookable.",
        }
    time_norm = time.strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_norm):
        return {"available": False, "reason": f"'{time}' is not a valid 24h HH:MM time."}
    if not (OPENING_TIME <= time_norm <= CLOSING_TIME):
        return {
            "available": False,
            "reason": f"Viewings run {OPENING_TIME}-{CLOSING_TIME} — '{time_norm}' is outside opening hours.",
        }
    # Existence check via the untracked module function: passing an id through
    # check_availability must NOT count as "confirmed this turn".
    if inv.get_car_details(car_id) is None:
        return {"available": False, "reason": f"No car with id {car_id} in inventory."}
    return {"available": True, "car_id": car_id, "day": matched_day.capitalize(), "time": time_norm}


def propose_booking(car_id: int, day: str, time: str) -> dict[str, Any]:
    """Validate the slot and return the payload the API layer surfaces as
    `booking_prompt` for the user's yes/no. Writes nothing — persistence only
    happens in confirm_booking, after an explicit user yes."""
    seen = TURN_SEEN_CAR_IDS.get()
    if seen is not None and car_id not in seen:
        return {
            "error": (
                f"car_id {car_id} was not returned by search_inventory or "
                "get_car_details in this turn — call get_car_details first to "
                "confirm which car you are booking, then retry."
            )
        }
    slot = check_availability(car_id, day, time)
    if not slot["available"]:
        return {"status": "invalid", "reason": slot["reason"]}
    return {
        "status": "proposed",
        "booking_prompt": {"car_id": slot["car_id"], "day": slot["day"], "time": slot["time"]},
        "note": "Ask the user to confirm yes/no before calling confirm_booking.",
    }


def confirm_booking(
    car_id: int, day: str, time: str, session_id: str = "", user_id: int | None = None
) -> dict[str, Any]:
    session_id, user_id = _trusted_identity(session_id, user_id)
    slot = check_availability(car_id, day, time)
    if not slot["available"]:
        return {"status": "invalid", "reason": slot["reason"]}
    with get_connection() as conn:
        result = conn.execute(
            leads.insert().values(
                user_id=user_id,
                session_id=session_id,
                car_id=car_id,
                booking_day=slot["day"],
                booking_time=slot["time"],
                status="booking_confirmed",
            )
        )
    return {
        "status": "booking_confirmed",
        "lead_id": result.inserted_primary_key[0],
        "car_id": car_id,
        "day": slot["day"],
        "time": slot["time"],
    }


def save_lead(
    session_id: str = "",
    user_id: int | None = None,
    car_id: int | None = None,
    budget_range: str | None = None,
    needs_notes: str | None = None,
) -> dict[str, Any]:
    """Insert a 'captured' lead for this session, or update the session's
    existing captured lead as more qualification info arrives."""
    session_id, user_id = _trusted_identity(session_id, user_id)
    updates = {
        k: v
        for k, v in {
            "user_id": user_id, "car_id": car_id,
            "budget_range": budget_range, "needs_notes": needs_notes,
        }.items()
        if v is not None
    }
    with get_connection() as conn:
        existing = conn.execute(
            sa.select(leads.c.id)
            .where(leads.c.session_id == session_id, leads.c.status == "captured")
            .order_by(leads.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            conn.execute(leads.update().where(leads.c.id == existing).values(**updates))
            return {"status": "captured", "lead_id": existing, "updated": True}
        result = conn.execute(
            leads.insert().values(session_id=session_id, status="captured", **updates)
        )
    return {"status": "captured", "lead_id": result.inserted_primary_key[0], "updated": False}


TOOL_DISPATCH = {
    "search_inventory": search_inventory,
    "get_car_details": get_car_details,
    "check_availability": check_availability,
    "propose_booking": propose_booking,
    "confirm_booking": confirm_booking,
    "save_lead": save_lead,
}
