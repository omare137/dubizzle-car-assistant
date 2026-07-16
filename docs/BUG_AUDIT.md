# Bug Audit — Live Demo Findings (Phase 13)

Diagnostic pass only; no application logic was modified. All reproductions ran
against the real stack (SQLite + orchestrator + live Gemini calls). The
instrumentation (tool-call observer, `get_completion` wrapper) lives entirely
in the throwaway script `app/agent/_bug_audit_smoke.py`, so no temporary
prints exist in application code.

---

## Bug 1: Duplicate car cards

**Suspected symptom:** the same car (an Audi Q8) rendered 5× in one /chat
response during a compare conversation.

**Reproduction steps taken:** live turn "Compare the Audi Q8 and the BMW i4
for me" with every tool call's returned ids logged; plus a direct SQL audit of
the inventory table.

**Evidence gathered:**

Tool trace for the turn:
```
TOOL search_inventory({"make": "Audi", "model": "Q8"}) -> returned ids=[7, 39, 40, 41, 76]
TOOL search_inventory({"make": "BMW", "model": "i4"})  -> returned ids=[79, 95]
FINAL result['cars'] ids: [7, 39, 40, 41, 76, 79, 95]
```
All five Q8 rows carry the *same* title ("4,000 P.M | 0% DownPayment | Under
Warranty | SummerOffer AB…"), make, model, year, and price (204,999 AED) — but
**five distinct inventory ids**. SQL audit of exact duplicates
(`GROUP BY make, model, year, title HAVING COUNT(*) > 1`):

```
audi q8 2023          ×5  ids 7,39,40,41,76
bmw i4 2024           ×2  ids 79,95
haval h9 2026         ×2  ids 5,96
range rover evoque    ×2  ids 75,81
range rover sport '15 ×2  ids 13,62
nissan patrol 2024    ×2  ids 43,83
tova j14 2025         ×2  ids 68,69
```

**Does orchestrator dedup exist?** Yes — quoting `orchestrator.py`:
```python
cars: dict[int, dict] = {}
...
if name == "search_inventory" and isinstance(result, list):
    for car in result:
        cars[car["id"]] = car
elif name == "get_car_details" and isinstance(result, dict) and "id" in result:
    cars[result["id"]] = result
```
It keys by `id` and **it fired correctly**: each id appears exactly once in
the final list. It cannot collapse the five Q8s because they are five
different rows in the source dataset (duplicate/reposted marketplace
listings), not one row returned five times.

**Second sources checked:** `routes.py` maps 1:1
(`cars=[_to_card(car) for car in result["cars"]]` — no duplication added) and
the frontend (`chat.tsx`) renders `msg.cars.map(...)` as-is — neither layer
adds duplication, and neither dedupes; they faithfully render what arrives.

**Root cause verdict:** duplicate listings in the raw dataset itself
(identical ads ingested under multiple ids in Phase 1). Not an orchestrator,
API, or frontend bug. Any fix belongs in the data pipeline (content-hash dedup
at build_inventory time) or as a content-level dedup before display.

**Confidence:** confirmed.
**Layer needing fixing:** data pipeline (`app/db/build_inventory.py`) — or
none, if "multiple identical dealer listings" is considered faithful to the
marketplace.

---

## Bug 2: `price_aed_monthly` not reaching the frontend

**Suspected symptom:** chat text cites a real monthly price; the car card
shows "Price on request".

**Reproduction steps taken:** traced car id 97 (GLA 35 AMG) through all four
layers; curled the live `/chat` endpoint.

**Evidence gathered:**

Layer 1 — `inventory_search.get_car_details(97)` **has the value**:
```
price_aed_monthly = 3819 | price_aed_cash = None | price_unlisted = True
```
Layer 2 — orchestrator stores the tool dicts unmodified (see Bug 1 quote), so
`price_aed_monthly` is still present in `result["cars"]`.

Layer 3 — `routes.py`'s `CarCard`, quoted as it currently exists:
```python
class CarCard(BaseModel):
    id: int
    make: str
    model: str
    year: int
    title: str
    price_aed_cash: int | None
    price_unlisted: bool
    photo_url: str
```
No `price_aed_monthly` field — `_to_card()` never copies it.

Layer 4 — raw HTTP response from the live endpoint (message: "Tell me about
the GLA 35 AMG and what it costs per month"):
```json
"reply": "... It is available for **AED 3,819 per month** ...",
"cars": [ ..., {
  "id": 97, "title": "3,819 P.M | 0% Downpayment | GLA 35 AMG | ...",
  "price_aed_cash": null, "price_unlisted": true,
  /* no price_aed_monthly key */ } ]
```
Exact demo symptom reproduced in one shot: reply says 3,819/month, card data
forces "Price on request".

**Root cause verdict:** the field is dropped at the **API mapping layer** —
`CarCard` (and therefore the Phase 4 contract, and the frontend's `ApiCar`
which mirrors it) never included `price_aed_monthly`. The contract was
designed before monthly-price coverage was known. Everything upstream carries
the value intact.

**Confidence:** confirmed.
**Layer needing fixing:** API mapping (`routes.py` CarCard + contract doc) and
frontend (`api.ts` `ApiCar` + `CarCard.tsx` price label) together.

---

## Bug 3: `propose_booking` not called in the same turn as the offer

**Suspected symptom:** agent offers a slot in plain text ("Would Saturday at
10am work?") with no tool call; `booking_prompt` stays null until the user
says yes.

**Reproduction steps taken:** identical two-turn conversation run 3×
(fresh guest session each): turn 1 "Tell me about the 2023 Toyota Prado GXR",
turn 2 "That sounds great, I'd love to come see it this week". Full tool
trace printed per turn. (Runs paced to respect the Gemini free-tier 15
req/min limit; one run was re-executed after a 429.)

**Evidence gathered (per run, not averaged):**

- **Run 1:** turn 2 called `search_inventory` then
  `propose_booking(car_id=67, day=Wednesday, time=10:00)` →
  `booking_prompt: {car_id: 67, day: Wednesday, time: 10:00}`. Reply asked
  "Would you be available to come see it this Wednesday at 10:00?" ✅ tool
  called in the offer turn.
- **Run 2:** same — `propose_booking` called, `booking_prompt` populated. ✅
- **Run 3:** turn 2 called **only** `search_inventory`; reply still said
  "Would this Wednesday at 10:00 work for you to come by and see it?" —
  `booking_prompt: None`. ❌ the offer happened entirely in plain text with
  no booking tool call. No `propose_booking` occurred in any earlier turn
  either (turn 1's only call was `search_inventory`).

**Root cause verdict:** intermittent instruction-following failure (2/3
compliant, 1/3 not). The context and tools are available; the model sometimes
narrates the offer without invoking `propose_booking`, so the frontend's
yes/no confirmation UI never appears for those turns. The system prompt says
to "call propose_booking with a concrete slot" but the model can satisfy the
conversational goal without it.

**Confidence:** confirmed (as intermittent — reproduced 1 in 3).
**Layer needing fixing:** system prompt (and/or tool description) — tighten
the requirement that any concrete slot offer MUST go through propose_booking
in the same turn. Orchestrator/API/frontend behave correctly when the tool is
called.

---

## Bug 4: "I found X for you" phrasing on follow-ups

**Suspected symptom:** follow-up about an already-shown car phrased as a
fresh discovery.

**Reproduction steps taken:** two-turn conversation ("Show me BMWs under 200k
AED" → "Tell me more about the i4") with a wrapper around
`orchestrator.get_completion` capturing the exact messages array sent on
turn 2's first LLM call.

**Evidence gathered:**

Exact messages array sent to the LLM on turn 2 (contents truncated for
display, roles and order verbatim):
```
[system]    You are the dubizzle cars assistant — ... (full system prompt)
[user]      Show me BMWs under 200k AED
[assistant] I found a few BMWs under 200,000 AED for you: * 2025 BMW M2 35: ...
            * 2024 BMW i4 Gran Coupé eDrive35 M Sport: 17,138 km, priced at 179,000 AED ...
[user]      Tell me more about the i4
```
Turn 1's full exchange **is present and correct** in turn 2's context —
short-term memory works exactly per Phase 7's design. In this reproduction,
turn 2's reply actually phrased well ("The 2024 BMW i4 Gran Coupé eDrive35 M
Sport is a fantastic electric option. Here are the key details…") — no "I
found" framing. Note the agent re-searches on follow-ups by design (tool
results aren't replayed across turns), which is the likely source of "I
found" narration when it does occur: the model narrates its fresh
search rather than the conversational continuity.

**Root cause verdict:** memory/context bug **ruled out** — the car is
genuinely in the history sent to the model. When the phrasing occurs it is a
style/instruction-following issue, likely triggered by the (by-design)
re-search on follow-up turns. Did not reproduce in this run, consistent with
it being occasional.

**Confidence:** confirmed that context is intact (memory exonerated);
inconclusive on how often the phrasing occurs (0/1 in this session; observed
in the live demo).
**Layer needing fixing:** system prompt (style guidance for follow-ups), or
none if judged cosmetic.

---

## Appendix: raw artifacts

- Diagnostic script: `app/agent/_bug_audit_smoke.py` (`bug1|bug3|bug4` modes);
  Bug 2 used direct function calls plus `curl` against the running backend.
- Bug 3 rate-limit note: the free tier allows 15 requests/min on
  `gemini-3.1-flash-lite`; the initial 3-run batch tripped a 429
  (`RESOURCE_EXHAUSTED ... Please retry in 32s`), surfaced correctly as our
  `LLMError`. Run 3 above is the paced re-execution.
- All reproduction sessions were guest sessions and their rows
  (sessions/messages/leads) were deleted after each run.
