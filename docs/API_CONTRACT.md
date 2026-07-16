# Dubizzle Car Assistant — API Contract

Stable contract for the FastAPI backend, for parallel frontend development
(e.g. a Lovable scaffold). Endpoints are live as **stubs returning realistic
mock JSON in exactly these shapes**; real logic lands in later phases without
changing the shapes.

Base URL (local dev): `http://127.0.0.1:8000`
Interactive docs (auto-generated): `GET /docs`

---

## Shared object: Car card

Returned by `/chat` (in `cars`) and `/inventory/search` (in `results`).

```json
{
  "id": 6,
  "make": "mercedes-benz",
  "model": "c-class",
  "year": 2021,
  "title": "AED 1611/month | 2021 Mercedes-Benz C-Class C200 | GCC Specs",
  "price_aed_cash": 106000,
  "price_aed_monthly": 1611,
  "price_unlisted": false,
  "photo_url": "https://dbz-images.dubizzle.com/images/..."
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | int | Stable inventory id — use for follow-up queries/bookings |
| `make`, `model` | string | Lowercase, as stored in inventory |
| `year` | int | |
| `title` | string | Listing headline |
| `price_aed_cash` | int \| null | Cash price in AED; null when none could be extracted from the listing |
| `price_aed_monthly` | int \| null | Monthly finance figure in AED; null when none listed |
| `price_unlisted` | bool | `true` ⇔ `price_aed_cash` is null (a monthly figure may still exist) |
| `photo_url` | string | Direct image URL for the card |

**Price display priority:** `price_aed_cash` ("AED 106,000") → else
`price_aed_monthly` ("AED 1,611/month") → else "Price on request". Never
render 0 or "free".

---

## 1. POST /session

Mock login / start a chat session. If `username` matches an existing user,
their long-term profile is loaded; otherwise an anonymous guest session starts.

**Request** (body optional entirely — send `{}` or omit `username` for guest):

```json
{ "username": "omar.k" }
```

**Response 200:**

```json
{
  "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "user": { "id": 1, "name": "Omar Khalid" },
  "returning_user": true,
  "profile_summary": "Previously looking for a white SUV under 200k AED"
}
```

Guest variant: `"user": null, "returning_user": false, "profile_summary": null`.
An unknown `username` behaves as guest (no error).

**Status codes:** `200` created; `422` malformed body.

---

## 2. POST /chat

Send one user message in an existing session; get the agent's reply.

**Request:**

```json
{
  "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "Show me some Mercedes under 150k"
}
```

**Response 200:**

```json
{
  "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "reply": "Here are a few options within your budget...",
  "cars": [ { "...": "Car card — see shared object above" } ],
  "booking_prompt": null
}
```

| Field | Type | Notes |
|---|---|---|
| `reply` | string | The agent's natural-language answer — always present |
| `cars` | Car[] | Populated **only** when the reply references specific listings; otherwise `[]` |
| `booking_prompt` | object \| null | Set when the agent proposes a viewing slot: `{ "car_id": 6, "day": "Wednesday", "time": "14:00" }` |

**Status codes:** `200` OK; `404` unknown `session_id`; `422` malformed body.

> **Frontend note — the key one:** `cars` and `booking_prompt` are the two
> fields to render specially. Render `cars` as car cards (photo from
> `photo_url`, title, year, price — honoring `price_unlisted`). When
> `booking_prompt` is non-null, render a **yes/no confirmation UI** for the
> proposed slot; the user's answer goes back through `POST /chat` as a normal
> message (e.g. "yes, Wednesday at 14:00 works"). Everything else is plain
> chat text.

---

## 3. GET /inventory/search

Direct inventory query — mainly for debugging/demo screenshots, not the
primary chat path.

**Query params** (all optional): `make`, `model` (partial, case-insensitive),
`year_min`, `year_max` (inclusive ints), `price_max` (int, AED),
`keywords` (comma-separated terms, OR-matched), `limit` (int, default 10).

Example: `GET /inventory/search?make=mercedes&price_max=150000&keywords=sunroof,camera&limit=5`

**Response 200:**

```json
{
  "results": [ { "...": "Car card — see shared object above" } ],
  "count": 1
}
```

Note: `price_max` does **not** exclude cars with no extracted price — they are
returned with `price_unlisted: true` (deliberate; see backend docs).

**Status codes:** `200` OK (empty `results` + `count: 0` when nothing matches);
`422` non-integer numeric params.

---

## 4. POST /session/{session_id}/end

Explicitly end a session. Triggers the long-term memory compiler (Phase 8;
stubbed for now).

**Request:** no body.

**Response 200:**

```json
{ "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "ended": true }
```

**Status codes:** `200` OK; `404` unknown `session_id` (once wired).

---

## 5. GET /health

Liveness check (exists since Phase 0).

**Response 200:**

```json
{ "status": "ok" }
```

---

## Error format

All errors use FastAPI's standard shape with an appropriate 4xx/5xx status:

```json
{ "detail": "message" }
```

Validation errors (`422`) use FastAPI's standard `detail` array of field
errors. The frontend can uniformly show `detail` (stringified) as the error
message.
