"""Long-term memory: compile a session transcript into the user's `profiles`
row, and format it back into a profile_summary for future sessions.
"""

import json
import logging
import re
from typing import Any

import sqlalchemy as sa

from app.agent.llm_client import get_completion
from app.agent.memory import get_session_user, load_session_messages
from app.db.database import get_connection
from app.db.schema import inventory, profiles, utcnow

logger = logging.getLogger(__name__)

EMPTY_PREFS: dict[str, Any] = {
    "budget_range": None,
    "preferred_makes": [],
    "preferred_body_type": None,
    "cars_of_interest": [],
    "notes": None,
}

SUMMARIZER_PROMPT = """\
You extract structured car-shopping preferences from a dealership chat \
transcript.

Only include information the user actually stated or clearly engaged with. \
Do not infer preferences that weren't expressed. If nothing meaningful was \
discussed, return an empty preferences object.

Respond with ONLY strict JSON (no markdown fences, no commentary) matching:
{
  "budget_range": string or null,        // e.g. "under 150,000 AED", only if stated
  "preferred_makes": [string],           // makes the user asked for or favored
  "preferred_body_type": string or null, // e.g. "SUV", only if expressed
  "cars_of_interest": [string],          // names/titles of specific cars the user
                                         // showed real interest in (asked for details,
                                         // wanted a viewing), exactly as mentioned
  "notes": string or null                // other explicit preferences (booking made or
                                         // proposed, must-have features), one short sentence
}
"""


def _parse_llm_json(text: str) -> dict | None:
    """Parse the summarizer output defensively (fences, malformed JSON)."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Profile summarizer returned unparseable JSON: %.200s", text)
        return None
    if not isinstance(data, dict):
        logger.warning("Profile summarizer returned non-object JSON: %.200s", text)
        return None
    return data


def _resolve_car_mentions(mentions: list) -> list[int]:
    """Ground car names/titles from the summarizer to real inventory ids.

    The transcript never contains numeric ids, so we never ask the LLM for
    them — mentions that don't match a real listing are dropped, which is
    what keeps invented cars out of the profile.
    """
    ids: list[int] = []
    with get_connection() as conn:
        for mention in mentions:
            if not isinstance(mention, str) or not mention.strip():
                continue
            car_id = conn.execute(
                sa.select(inventory.c.id)
                .where(inventory.c.title.ilike(f"%{mention.strip()}%"))
                .order_by(inventory.c.id)
                .limit(1)
            ).scalar_one_or_none()
            if car_id is None:
                # Fall back to model-name match only ("BZ4X" inside "2025
                # Toyota BZ4X"). Model column only — matching title tokens is
                # too loose (generic words like "Special" appear in titles).
                for token in mention.split():
                    if len(token) < 4 or token.isdigit():
                        continue
                    car_id = conn.execute(
                        sa.select(inventory.c.id)
                        .where(inventory.c.model.ilike(f"%{token}%"))
                        .order_by(inventory.c.id)
                        .limit(1)
                    ).scalar_one_or_none()
                    if car_id is not None:
                        break
            if car_id is not None and car_id not in ids:
                ids.append(car_id)
    return ids


def _normalize(raw: dict) -> dict[str, Any]:
    """Coerce the summarizer output into the storage schema."""
    makes = raw.get("preferred_makes") or []
    prefs = {
        "budget_range": raw.get("budget_range") or None,
        "preferred_makes": sorted(
            {m.strip().lower() for m in makes if isinstance(m, str) and m.strip()}
        ),
        "preferred_body_type": raw.get("preferred_body_type") or None,
        "cars_of_interest": _resolve_car_mentions(raw.get("cars_of_interest") or []),
        "notes": raw.get("notes") or None,
    }
    return prefs


def _is_empty(prefs: dict) -> bool:
    return not any(
        [prefs.get("budget_range"), prefs.get("preferred_makes"),
         prefs.get("preferred_body_type"), prefs.get("cars_of_interest"), prefs.get("notes")]
    )


def merge_preferences(old: dict | None, new: dict) -> dict[str, Any]:
    """Merge a newly compiled profile into the existing one (never blind-overwrite)."""
    old = {**EMPTY_PREFS, **(old or {})}
    merged = dict(old)
    merged["preferred_makes"] = sorted(set(old["preferred_makes"]) | set(new["preferred_makes"]))
    merged["cars_of_interest"] = sorted(set(old["cars_of_interest"]) | set(new["cars_of_interest"]))
    merged["budget_range"] = new["budget_range"] or old["budget_range"]
    merged["preferred_body_type"] = new["preferred_body_type"] or old["preferred_body_type"]
    if new["notes"] and (not old["notes"] or new["notes"] not in old["notes"]):
        merged["notes"] = f"{old['notes']} {new['notes']}".strip() if old["notes"] else new["notes"]
    return merged


def _load_profile(user_id: int) -> dict | None:
    with get_connection() as conn:
        raw = conn.execute(
            sa.select(profiles.c.preferences_json).where(profiles.c.user_id == user_id)
        ).scalar_one_or_none()
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupt preferences_json for user %s; treating as empty", user_id)
        return None


def _upsert_profile(user_id: int, prefs: dict) -> None:
    payload = json.dumps(prefs, ensure_ascii=False)
    with get_connection() as conn:
        existing = conn.execute(
            sa.select(profiles.c.user_id).where(profiles.c.user_id == user_id)
        ).scalar_one_or_none()
        if existing is None:
            conn.execute(
                profiles.insert().values(
                    user_id=user_id, preferences_json=payload, last_updated=utcnow()
                )
            )
        else:
            conn.execute(
                profiles.update()
                .where(profiles.c.user_id == user_id)
                .values(preferences_json=payload, last_updated=utcnow())
            )


def compile_session_to_profile(session_id: str) -> dict | None:
    """Distill one session's transcript into the user's persistent profile.

    Guest sessions and empty/one-sided transcripts return None. The result is
    MERGED with any existing profile, upserted, and returned.
    """
    user = get_session_user(session_id)
    if user is None:  # guest or unknown session — never profile anonymous users
        return None

    transcript = load_session_messages(session_id)
    if not transcript or not any(m["role"] == "assistant" for m in transcript):
        return None

    transcript_text = "\n".join(f"{m['role']}: {m['content']}" for m in transcript)
    response = get_completion(
        [
            {"role": "system", "content": SUMMARIZER_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
        ]
    )
    raw = _parse_llm_json(response.choices[0].message.content or "")
    if raw is None:
        return None

    new_prefs = _normalize(raw)
    merged = merge_preferences(_load_profile(user["id"]), new_prefs)
    if _is_empty(merged):
        return None
    _upsert_profile(user["id"], merged)
    return merged


def get_profile_summary(user_id: int) -> str | None:
    """Format the stored profile into 1-2 natural-language sentences for the
    system prompt. Pure templating — no LLM call."""
    prefs = _load_profile(user_id)
    if prefs is None or _is_empty({**EMPTY_PREFS, **prefs}):
        return None

    first: list[str] = []
    if prefs.get("preferred_body_type"):
        first.append(f"{prefs['preferred_body_type']}s")
    if prefs.get("preferred_makes"):
        first.append(f"from {', '.join(prefs['preferred_makes'])}")
    if prefs.get("budget_range"):
        first.append(prefs["budget_range"])
    sentences = []
    if first:
        sentences.append(f"Previously interested in {' '.join(first)}.")

    car_ids = prefs.get("cars_of_interest") or []
    if car_ids:
        with get_connection() as conn:
            # year/make/model, not title — titles often lead with finance
            # figures ("AED 2279/month | ..."), which reads badly here.
            names = [
                f"{r.year} {r.make.title()} {r.model.title()}"
                for r in conn.execute(
                    sa.select(inventory.c.year, inventory.c.make, inventory.c.model)
                    .where(inventory.c.id.in_(car_ids))
                    .order_by(inventory.c.id)
                )
            ]
        if names:
            sentences.append(f"Showed interest in {' and '.join(names[:3])}.")

    if prefs.get("notes"):
        sentences.append(prefs["notes"].rstrip(".") + ".")

    return " ".join(sentences) if sentences else None
