# Dubizzle Car Assistant

An AI chat assistant over dubizzle's used-car inventory: users search, compare, and book
viewings for real listings through conversation, backed by a FastAPI + SQLite backend and
a TanStack Start chat frontend.

## Setup & execution

### Backend

```bash
uv sync
cp .env.example .env          # then set GEMINI_API_KEY (get one at aistudio.google.com/apikey)

# One-time DB setup (idempotent — safe to re-run):
uv run python -m app.db.init_db          # creates tables, seeds 3 mock users
uv run python -m app.db.build_inventory  # cleans data/raw/*.xlsx -> data/processed/app.db

uv run uvicorn app.main:app --reload     # http://127.0.0.1:8000, docs at /docs
```

Optional, not required for `/chat` to work: `uv run python -m app.rag.embed_inventory`
builds local embeddings for the shadow semantic-search pipeline (see "Why this stack"
below). Without it, that background pipeline logs a warning and degrades to SQL-only —
the live chat endpoint is unaffected either way.

### Frontend

```bash
cd frontend
bun install
bun run dev    # http://localhost:8080
```

Needs the backend running at `http://127.0.0.1:8000` — the backend's CORS middleware
(`app/main.py`) only allows `frontend_origin` from `.env` (default `http://localhost:8080`)
plus its `127.0.0.1` twin, so a different port or host needs that setting updated on both
sides.

## Why this stack

The frontend is a real chat UI (TanStack Start/React) rather than a Streamlit or notebook
demo, because the coursework's proof points — multi-turn conversation, follow-up
resolution, returning-user recall, a booking confirmation flow — are fundamentally
conversational UX, not something a form-based notebook interface demonstrates well. The
agent is a single LLM-driven orchestrator with tool-calling (LiteLLM wrapping Gemini),
not a multi-agent handoff system: one model, one system prompt, a fixed set of tools
(`search_inventory`, `get_car_details`, `check_availability`, `propose_booking`,
`confirm_booking`, `save_lead`) called in a loop until it produces a final reply — simpler
to reason about and debug than routing between specialized agents, and the task doesn't
need more than one point of judgment. Retrieval is deterministic SQL (`ILIKE` filters over
make/model/year/price/keywords) — the only thing the LLM can actually trigger — but results
are re-presented through a second LLM pass (the "intelligence agent") before the
conversational model reads them, trading raw rows for a grounded summary + body-type
classification while keeping the deterministic price/mileage columns intact. A separate
semantic-search + cross-reference pipeline runs in the background on every retrieval turn,
purely to log where LLM-extracted fields disagree with the deterministic ones — it doesn't
affect what the user sees yet (see Design decisions). Memory is SQLite for both layers:
short-term (the plain-text transcript for the current session, replayed into every turn)
and long-term (a compiled per-user profile as JSON, produced by a separate summarization
call, not automatically after every message).

## Design decisions & scope

**What was built, and why.** The core philosophy is grounding-first: the agent must never
state a price, spec, or availability fact that didn't come from an actual tool call this
turn — car facts are stapled to real inventory rows, and "I don't have that" is always a
valid, expected answer (verified live: an unavailable make gets an honest "we don't have
any," not an invented listing). A single orchestrator with tools was chosen over a
multi-agent pipeline because the task — search, answer questions, propose and confirm a
viewing — is one continuous conversation with one consistent voice; splitting it into
specialist agents would add coordination overhead without adding capability. Long-term
memory compilation is explicit and triggered (on session end), not run after every
message, both to control LLM call volume and because a profile compiled from a
half-finished conversation is worse than no profile at all.

**What's out of scope / left for future work.** Unpriced cars (`price_unlisted=true`) are
flagged in the data and the agent is prompted to call this out, but there's no UI
treatment distinguishing them from priced listings — a real product would visually
de-emphasize or badge them rather than relying on the reply text alone. Booking is a
day/time text exchange validated against a fixed weekly schedule (Mon–Sat, 08:00–20:00);
it has no real calendar backing and can't detect or prevent two users double-booking the
same slot for the same car. Car cards have no outbound link to the actual dubizzle listing
page — `photo_url` is the only source URL carried through. There's no path beyond "book a
viewing": no financing-interest capture, trade-in flow, or "I'm ready to buy" signal for
the sales team, though `save_lead` captures budget/needs as a first step toward that.
Frontend session state (the `session_id`) isn't persisted across a browser refresh, so
reloading starts a fresh anonymous session even mid-conversation. Cross-turn car identity
is deliberately not persisted (see Known limitations) — resolving it structurally (the
model declaring car ids explicitly, carried in state rather than re-derived from text) is
the natural next step. And deterministic price/mileage extraction only covers a minority
of listings (see numbers below); widening those regexes against the actual failure modes
in the raw data would directly improve how often the agent can quote a real price instead
of "price on request."

## Known limitations

- **Cross-turn car identity isn't fully persisted.** Only the human-readable transcript
  (user + assistant text) is replayed across turns — tool-call results are not. This is a
  deliberate simplicity trade-off, not an oversight, but it has two consequences: (1) if
  the model recaps a car by name from its own earlier reply without re-searching, the
  orchestrator has no tool-confirmed row for it that turn, so a card can't be rendered
  from thin air; the system prompt/tool description compensates by instructing the model
  to re-fetch via `get_car_details` before booking, which is enforced (`propose_booking`
  rejects any `car_id` not tool-confirmed in the current turn). (2) Any UI element tied to
  "the car under discussion" only has as much information as this turn's own tool calls.
- **Price/mileage extraction coverage is partial.** Of 100 inventory rows, 33 have an
  extracted cash price, 35 a monthly instalment, 38 have either, and 55 have a mileage
  figure — the rest are genuinely ambiguous or absent in the source listing text, and the
  extractor is intentionally conservative (nulls on any uncertainty rather than guessing).
  The agent is prompted to say "price not listed" rather than hide these rows.
- **The live search method is keyword/filter matching, not semantic.** `search_inventory`
  is SQL `ILIKE` over make/model/keywords — a user asking for "a family car with lots of
  boot space" without naming a body type or make gets weaker results than "Toyota SUV,"
  since there's no vector search in the live retrieval path (only in the background
  shadow pipeline, which doesn't feed the reply). This is the most direct lever for
  improving recall on vague queries.

## Screenshots

Both proofs required by the coursework are covered below: a successful multi-turn
conversation exploring inventory (2–9), and the agent recalling a returning user's
previous preferences in a brand-new session (1, 10, 11 — three separate instances, at
increasing levels of profile detail).

![Login screen](evidence/Screenshot%202026-07-16%20at%207.37.16%20PM.png)
Login screen — signing in as an existing seeded user (`omar.k`).

![Returning-user recall banner](evidence/Screenshot%202026-07-16%20at%207.37.41%20PM.png)
**Proof 2 (returning-user recall):** a brand-new session opens with "Welcome back — Omar
Khalid," summarizing preferences compiled from an earlier, separate session.

![SUV search results](evidence/Screenshot%202026-07-16%20at%2010.16.49%20PM.png)
**Proof 1 (multi-turn conversation):** a filtered SUV search returns real inventory cards
with prices.

![Follow-up "tell me more about the first one"](evidence/Screenshot%202026-07-16%20at%2010.17.08%20PM.png)
Cross-turn context resolution — "the first one" is correctly resolved to the earlier
result without the user repeating the car's name.

![Booking proposal widget](evidence/Screenshot%202026-07-16%20at%2010.17.47%20PM.png)
Booking flow: the agent proposes a specific viewing slot with a Yes/No confirmation
widget, not an auto-confirmed booking.

![Booking slot renegotiation](evidence/Screenshot%202026-07-16%20at%2010.19.41%20PM.png)
The user rejects the proposed time in plain text ("thursday 10:30 instead") and the
widget updates to match the new slot.

![Off-topic request refused](evidence/Screenshot%202026-07-16%20at%2010.20.23%20PM.png)
Guardrail: an off-topic coding request is politely declined and the conversation is
steered back to the pending booking.

![Booking confirmed via plain text](evidence/Screenshot%202026-07-16%20at%2010.20.55%20PM.png)
Booking confirmed via a plain-text "ok fine i confirm the viewing" — the yes/no flow
isn't limited to clicking the widget button.

![Honest "we don't have any" answer](evidence/Screenshot%202026-07-16%20at%2010.21.46%20PM.png)
Grounding guardrail: asked for a make not in inventory (Lamborghini), the agent says so
plainly instead of fabricating a listing.

![Second recall, later session](evidence/Screenshot%202026-07-16%20at%2010.23.27%20PM.png)
**Proof 2 (returning-user recall), second instance:** another new session, now recalling
the confirmed booking alongside the original preferences.

![Recall answers a direct memory question](evidence/Screenshot%202026-07-16%20at%2010.24.00%20PM.png)
**Proof 2 (returning-user recall), strongest instance:** in that same new session, asked
directly "do you remember which one I booked," the agent answers correctly from the
compiled profile — not just displaying the banner, but using the recalled fact.

![Narrow filter, single exact match](evidence/Screenshot%202026-07-16%20at%2011.43.26%20PM.png)
A narrow query ("best SUVs before 2013") correctly returns exactly one matching listing
rather than padding the result with near-misses.
