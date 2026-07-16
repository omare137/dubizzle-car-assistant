"""Single LLM-driven orchestrator with a tool-calling loop.

Stateless w.r.t. persistence: it never touches the sessions/messages tables —
the API layer owns that (Phase 9).

Search results pass through the intelligence agent before the conversational
LLM reads them: search_inventory tool messages carry its structured entries
(summary description, body_type) instead of raw rows. The API `cars` payload
and get_car_details (whose full description grounds detail Q&A) keep the raw
rows, so the card contract and user-facing behaviour are unchanged.
"""

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from app.agent.intelligence_agent import extract_car_info
from app.agent.llm_client import LLMError, get_completion
from app.agent.system_prompt import build_system_prompt
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.agent.tools_impl import TOOL_DISPATCH, TURN_SEEN_CAR_IDS, check_availability

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# Make/model tokens too generic to prove a car was named in the reply
# ("first-class service" must not match a C-Class).
_MENTION_TOKEN_STOPLIST = {"class", "car", "cars", "auto", "new", "other", "sport"}

# Title tokens that look like variant codes but are shared spec/drivetrain
# jargon — one car's "4MATIC" in the reply must not vouch for another car
# whose title also carries it.
_TITLE_CODE_STOPLIST = {"4matic", "4wd", "2wd", "4x4", "0km", "v6", "v8", "v12"}

# Engine-spec tokens shaped like short variant codes but not model-identifying:
# displacement ("2.4L" -> "4l") and cylinder config ("v6"). Excluded so a
# 2-char code like "M2" counts while "4l"/"v6" don't.
_SPEC_CODE_RE = re.compile(r"\d+l|v\d+")


def _is_title_variant_code(tok: str) -> bool:
    """A title token that identifies a model variant ('330i', 'M2', 'F430') —
    used when the reply names the variant but the model column holds the family
    name ('3-series', '2-series'). 2 chars is enough ('M2', 'X1'); engine-spec
    look-alikes ('4l', 'v6') and shared drivetrain jargon ('4MATIC') are
    excluded so one car's spec can't vouch for another."""
    return (
        len(tok) >= 2
        and any(ch.isdigit() for ch in tok)
        and any(ch.isalpha() for ch in tok)
        and tok not in _TITLE_CODE_STOPLIST
        and _SPEC_CODE_RE.fullmatch(tok) is None
    )


def _word_pos(token: str, reply_low: str) -> int | None:
    """First position of token as a word in the reply (optionally pluralized,
    'BMWs'), or None."""
    m = re.search(rf"\b{re.escape(token)}s?\b", reply_low)
    return m.start() if m else None


def _distinctive_tokens(value: str) -> set[str]:
    """Tokens of a make/model that can evidence a mention. Drops stoplist
    words and short/purely-numeric fragments ('70' in 'land cruiser 70
    series' would match any price digits), but keeps '911'-style 3+ digit
    model names."""
    tokens = set()
    for tok in re.split(r"[^a-z0-9]+", value.lower()):
        if len(tok) < 2 or tok in _MENTION_TOKEN_STOPLIST:
            continue
        if tok.isdigit() and len(tok) < 3:
            continue
        tokens.add(tok)
    return tokens


def _model_mention_pos(car: dict, reply_low: str) -> int | None:
    """Position where the reply first names this car, or None. Evidence:

    - the whole model, separator-tolerant ('C-Class' matches 'c class' /
      'c-class' — its individual tokens are too generic on their own);
    - at least half the model's distinctive tokens as words (so 'Urban
      Cruiser' doesn't claim the Land Cruiser 70 Series via 'cruiser');
    - a variant code from the listing title ('330i', 'V220d', 'F430') —
      replies often use the variant name while the model column holds the
      family name ('3-series', 'v-class').
    """
    positions: list[int] = []

    chunks = [c for c in re.split(r"[^a-z0-9]+", str(car["model"]).lower()) if c]
    if chunks and len("".join(chunks)) >= 2:
        m = re.search(r"[^a-z0-9]*".join(map(re.escape, chunks)), reply_low)
        if m:
            positions.append(m.start())

    tokens = _distinctive_tokens(str(car["model"]))
    hits = [p for t in tokens if (p := _word_pos(t, reply_low)) is not None]
    if tokens and len(hits) / len(tokens) >= 0.5:
        positions.extend(hits)

    for tok in set(re.split(r"[^a-z0-9]+", str(car.get("title", "")).lower())):
        if _is_title_variant_code(tok) and (p := _word_pos(tok, reply_low)) is not None:
            positions.append(p)

    return min(positions) if positions else None


def _make_mention_pos(car: dict, reply_low: str) -> int | None:
    hits = [
        p for t in _distinctive_tokens(str(car["make"]))
        if (p := _word_pos(t, reply_low)) is not None
    ]
    return min(hits) if hits else None


def _cars_mentioned_in_reply(
    cars: dict[int, dict], reply: str, booking_prompt: dict | None
) -> list[dict]:
    """Cards for cars the reply actually references, in the order the reply
    first names them — so the photos track the reading order of the text.
    (The API contract's stated semantics for `cars`; search tools can return
    rows the model never mentions — observed live: a price-capped SUV search
    returned an unlisted-price Ferrari the reply ignored, and its card
    rendered anyway. Those must not become cards.)

    Model/variant matching is primary. Make matching is only a fallback for
    replies that name no models at all ("I found a few BMWs below") —
    otherwise a generic make mention ("our Toyota SUVs") would drag in every
    searched car of that make alongside the ones actually recommended.
    The booking_prompt car is always kept (first, when the reply doesn't
    name it): the yes/no confirmation UI refers to it either way."""
    booked_id = booking_prompt.get("car_id") if booking_prompt else None
    reply_low = reply.lower()

    mention_pos: dict[int, int] = {}
    for car in cars.values():
        pos = _model_mention_pos(car, reply_low)
        if pos is not None:
            mention_pos[car["id"]] = pos
    if not mention_pos:
        for car in cars.values():
            pos = _make_mention_pos(car, reply_low)
            if pos is not None:
                mention_pos[car["id"]] = pos
    if booked_id in cars and booked_id not in mention_pos:
        mention_pos[booked_id] = -1  # booked car leads when unnamed

    kept = sorted(
        (car for car in cars.values() if car["id"] in mention_pos),
        key=lambda c: mention_pos[c["id"]],
    )
    if len(kept) != len(cars):
        logger.info(
            "cards filtered to reply mentions: kept=%s dropped=%s",
            [c["id"] for c in kept], sorted(set(cars) - set(mention_pos)),
        )
    return kept


# Day + time slot mentions in reply prose, both orders ("Thursday at 7:30 PM",
# "7:30 PM on Thursday"), 12h and 24h forms. The gap allows no digits and no
# sentence ends, so a day only pairs with the ADJACENT time — "Monday at
# 08:00, then 9:30 pm on Tuesday" must not pair Monday's time with Tuesday,
# and "we close at 8:00 PM. Thursday..." must not pair at all.
_DAYS_RE = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_TIME_RE = (
    r"(?:(?P<h12>\d{1,2})(?::(?P<m12>[0-5]\d))?\s*(?P<ap>[ap])\.?m\.?"
    r"|(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d))"
)
_SLOT_DAY_FIRST = re.compile(rf"\b(?P<day>{_DAYS_RE})\b[^.!?\n\d]{{0,30}}?{_TIME_RE}", re.IGNORECASE)
_SLOT_TIME_FIRST = re.compile(rf"{_TIME_RE}[^.!?\n\d]{{0,20}}?\b(?P<day>{_DAYS_RE})\b", re.IGNORECASE)


def _slot_time(m: re.Match) -> str | None:
    """Normalize a _TIME_RE match to 24h HH:MM; None for nonsense hours."""
    if m.group("h24") is not None:
        return f"{int(m.group('h24')):02d}:{m.group('m24')}"
    hour = int(m.group("h12"))
    if not 1 <= hour <= 12:
        return None
    hour %= 12
    if m.group("ap").lower() == "p":
        hour += 12
    return f"{hour:02d}:{m.group('m12') or '00'}"


def _slots_in_reply(reply: str) -> set[tuple[str, str]]:
    """All (day, HH:MM) viewing slots the reply text mentions."""
    slots = set()
    for pattern in (_SLOT_DAY_FIRST, _SLOT_TIME_FIRST):
        for m in pattern.finditer(reply):
            time = _slot_time(m)
            if time is not None:
                slots.add((m.group("day").lower(), time))
    return slots


def _reconcile_booking_prompt(booking_prompt: dict | None, reply: str) -> dict | None:
    """The confirmation widget must show the same slot the reply offers.

    Observed live: the model proposed 20:00 (accepted — closing time), then
    wrote "7:30 PM instead" in the reply without re-proposing, so the widget
    asked about a slot the text never offered. Reconcile:
    - the reply names the proposed slot (possibly among others) -> keep it;
    - the reply names exactly one different slot and it validates -> follow
      the text (the reply is what the user reads and confirms);
    - otherwise -> drop the widget; the plain-text yes/no flow still works.
    Replies naming no slot keep the prompt — there is nothing to contradict.
    """
    if booking_prompt is None:
        return None
    slots = _slots_in_reply(reply)
    if not slots:
        return booking_prompt
    proposed = (booking_prompt["day"].lower(), booking_prompt["time"])
    if proposed in slots:
        return booking_prompt
    if len(slots) == 1:
        day, time = next(iter(slots))
        slot = check_availability(booking_prompt["car_id"], day, time)
        if slot.get("available"):
            fixed = {"car_id": booking_prompt["car_id"], "day": slot["day"], "time": slot["time"]}
            logger.info("booking prompt corrected to match reply: %s -> %s", booking_prompt, fixed)
            return fixed
    logger.info(
        "booking prompt dropped: reply offers %s, tool proposed %s", sorted(slots), proposed
    )
    return None


# Fields taken verbatim from each intelligence-agent entry into the
# conversational LLM's view of a search result.
_INTEL_FIELDS = ("id", "make", "model", "trim", "year", "title", "description", "url", "body_type")


def _intelligence_view(rows: list[dict], user_query: str) -> list[dict]:
    """The conversational LLM's view of search results: the intelligence
    agent's structured entries instead of raw rows.

    The deterministic price split (cash/monthly/unlisted) and mileage_km stay
    from the raw row: the system prompt's grounding rules key off
    price_unlisted, and the agent's single extracted `price` cannot tell a
    monthly instalment from a cash price — so that field is not shown at all.
    Any row the agent fails to extract falls back to its raw form: a car must
    never vanish from the LLM's view while its card still renders.
    """
    try:
        extracted = {e["id"]: e for e in extract_car_info(rows, user_query)}
    except Exception:
        logger.warning("intelligence extraction failed; using raw rows", exc_info=True)
        return rows
    view: list[dict] = []
    for row in rows:
        entry = extracted.get(row["id"])
        if entry is None:
            view.append(row)
            continue
        view.append(
            {
                **{k: entry[k] for k in _INTEL_FIELDS},
                "price_aed_cash": row["price_aed_cash"],
                "price_aed_monthly": row["price_aed_monthly"],
                "price_unlisted": row["price_unlisted"],
                "mileage_km": row["mileage_km"],
            }
        )
    return view


def _dispatch(name: str, arguments: str) -> Any:
    """Run one tool call; return an error payload (not an exception) on
    failure so the LLM can read it and correct course."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'"}
    try:
        kwargs = json.loads(arguments) if arguments else {}
        return fn(**kwargs)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def run_agent_turn(
    messages: list[dict],
    profile_summary: str | None,
    on_tool_call: Callable[[str, dict, Any], None] | None = None,
) -> dict[str, Any]:
    """Run one full agent turn (LLM call + any tool calls) and return:

    { "reply": str, "cars": list[dict], "booking_prompt": dict | None,
      "updated_messages": list[dict] }

    `cars` = deduped cars returned by search_inventory/get_car_details during
    this turn; `booking_prompt` = payload of a successful propose_booking.
    `on_tool_call(name, args, result)` is an optional observer for debugging.
    """
    msgs = list(messages)
    if not msgs or msgs[0].get("role") != "system":
        msgs.insert(0, {"role": "system", "content": build_system_prompt(profile_summary)})

    cars: dict[int, dict] = {}
    booking_prompt: dict | None = None

    # Fresh per-turn set of tool-confirmed car ids; propose_booking rejects
    # ids outside it (guessed-id guard).
    seen_token = TURN_SEEN_CAR_IDS.set(set())
    try:
        return _run_loop(msgs, cars, booking_prompt, on_tool_call)
    finally:
        TURN_SEEN_CAR_IDS.reset(seen_token)


def _run_loop(
    msgs: list[dict],
    cars: dict[int, dict],
    booking_prompt: dict | None,
    on_tool_call: Callable[[str, dict, Any], None] | None,
) -> dict[str, Any]:
    # Latest user message — context for the intelligence agent's description
    # emphasis (never a source of facts; see its prompt).
    user_query = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")

    for _ in range(MAX_TOOL_ITERATIONS):
        response = get_completion(msgs, tools=TOOL_SCHEMAS)
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            reply = message.content or ""
            final_booking = _reconcile_booking_prompt(booking_prompt, reply)
            return {
                "reply": reply,
                "cars": _cars_mentioned_in_reply(cars, reply, final_booking),
                "booking_prompt": final_booking,
                "updated_messages": msgs + [{"role": "assistant", "content": reply}],
            }

        msgs.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            name = tc.function.name
            result = _dispatch(name, tc.function.arguments)
            if on_tool_call:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = tc.function.arguments
                on_tool_call(name, args, result)

            # What the conversational LLM reads for this call; raw `result`
            # still feeds the cards and the on_tool_call observer above.
            message_payload = result
            if name == "search_inventory" and isinstance(result, list):
                for car in result:
                    cars[car["id"]] = car
                if result:
                    message_payload = _intelligence_view(result, user_query)
            elif name == "get_car_details" and isinstance(result, dict) and "id" in result:
                cars[result["id"]] = result
            elif name == "propose_booking" and isinstance(result, dict):
                booking_prompt = result.get("booking_prompt", booking_prompt)

            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(message_payload, ensure_ascii=False, default=str),
                }
            )

    raise LLMError(
        f"Agent exceeded {MAX_TOOL_ITERATIONS} tool iterations without producing a final reply"
    )
