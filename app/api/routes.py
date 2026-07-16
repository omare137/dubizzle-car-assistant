"""API routes — real implementations over Phases 2-8.

Response shapes are the API contract (docs/API_CONTRACT.md) and must not
change; only the logic behind them.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.llm_client import LLMError
from app.agent.long_term_memory import get_profile_summary
from app.agent.retrieval_pipeline import shadow_run
from app.agent.memory import (
    create_session,
    end_session,
    get_session_user,
    get_user_by_username,
    run_turn_with_memory,
    session_exists,
)
from app.tools import inventory_search as inv

router = APIRouter()

# ---------------------------------------------------------------------------
# Contract models (shapes are the API contract — keep in sync with
# docs/API_CONTRACT.md)
# ---------------------------------------------------------------------------


class CarCard(BaseModel):
    id: int
    make: str
    model: str
    year: int
    title: str
    price_aed_cash: int | None
    price_aed_monthly: int | None
    price_unlisted: bool
    photo_url: str


class SessionRequest(BaseModel):
    username: str | None = None


class UserOut(BaseModel):
    id: int
    name: str


class SessionResponse(BaseModel):
    session_id: str
    user: UserOut | None
    returning_user: bool
    profile_summary: str | None


class ChatRequest(BaseModel):
    session_id: str
    message: str


class BookingPrompt(BaseModel):
    car_id: int
    day: str
    time: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    cars: list[CarCard]
    booking_prompt: BookingPrompt | None


class SearchResponse(BaseModel):
    results: list[CarCard]
    count: int


class EndSessionResponse(BaseModel):
    session_id: str
    ended: bool


def _to_card(car: dict) -> CarCard:
    """Internal car dict (search_inventory/get_car_details shape) -> CarCard."""
    return CarCard(
        id=car["id"],
        make=car["make"],
        model=car["model"],
        year=car["year"],
        title=car["title"],
        price_aed_cash=car["price_aed_cash"],
        price_aed_monthly=car["price_aed_monthly"],
        price_unlisted=car["price_unlisted"],
        photo_url=car["photo_url"],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/session", response_model=SessionResponse)
def start_session(body: SessionRequest | None = None) -> SessionResponse:
    username = body.username if body else None
    if not username:
        return SessionResponse(
            session_id=create_session(user_id=None),
            user=None,
            returning_user=False,
            profile_summary=None,
        )

    user = get_user_by_username(username)
    if user is None:
        # Explicit 404 rather than silent guest fallback — hides typos otherwise.
        raise HTTPException(status_code=404, detail="Unknown username")

    session_id = create_session(user_id=user["id"])
    profile_summary = get_profile_summary(user["id"])
    return SessionResponse(
        session_id=session_id,
        user=UserOut(id=user["id"], name=user["name"]),
        returning_user=profile_summary is not None,
        profile_summary=profile_summary,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    if not session_exists(body.session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    user = get_session_user(body.session_id)
    # Fetched fresh every call: cheap SQLite read, avoids staleness.
    profile_summary = get_profile_summary(user["id"]) if user else None

    # Shadow retrieval → intelligence pipeline: collect the rows the agent's
    # retrieval tools return this turn, then hand them (plus semantic matches)
    # to the intelligence agent off the request path. Observational only —
    # the reply and response shape are untouched.
    retrieved_rows: list[dict] = []
    retrieval_turn = False

    def _collect_retrieved(name: str, _args: dict, result: object) -> None:
        nonlocal retrieval_turn
        if name == "search_inventory" and isinstance(result, list):
            retrieval_turn = True
            retrieved_rows.extend(result)
        elif name == "get_car_details" and isinstance(result, dict) and "id" in result:
            retrieval_turn = True
            retrieved_rows.append(result)

    try:
        result = run_turn_with_memory(
            body.session_id, body.message, profile_summary, on_tool_call=_collect_retrieved
        )
    except LLMError:
        raise HTTPException(
            status_code=502, detail="Assistant is temporarily unavailable, please try again"
        ) from None

    if retrieval_turn:
        shadow_run(body.message, retrieved_rows)

    booking = result["booking_prompt"]
    return ChatResponse(
        session_id=body.session_id,
        reply=result["reply"],
        cars=[_to_card(car) for car in result["cars"]],
        booking_prompt=BookingPrompt(**booking) if booking else None,
    )


@router.get("/inventory/search", response_model=SearchResponse)
def inventory_search(
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    keywords: str | None = None,
    limit: int = 10,
) -> SearchResponse:
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    results = inv.search_inventory(
        make=make,
        model=model,
        year_min=year_min,
        year_max=year_max,
        price_max=price_max,
        keywords=keyword_list,
        limit=limit,
    )
    cards = [_to_card(car) for car in results]
    return SearchResponse(results=cards, count=len(cards))


@router.post("/session/{session_id}/end", response_model=EndSessionResponse)
def end_session_route(session_id: str) -> EndSessionResponse:
    if not session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        end_session(session_id)
    except LLMError:
        # ended_at is stamped before the profile compile runs; a failed
        # compile shouldn't fail the end call. The profile just doesn't
        # update this time.
        pass
    return EndSessionResponse(session_id=session_id, ended=True)
