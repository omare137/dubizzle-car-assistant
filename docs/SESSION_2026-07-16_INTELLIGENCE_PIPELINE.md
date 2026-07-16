# Session Notes — Intelligence Agent Pipeline (2026-07-16)

Everything implemented in this session, in order. Starting point: a single
tool-calling conversational agent over SQL retrieval; the intelligence agent,
semantic search, and cross-reference modules existed but were dormant (no
production caller). End state: retrieval results flow through the
intelligence agent before the conversational agent reads them, plus three
UX correctness fixes (card filtering, card ordering, booking-widget
reconciliation).

---

## 1. Architecture review (no code changes)

Full read of backend, frontend, DB, RAG modules, and docs. Key findings that
drove the later work:

- The conversational orchestrator ([app/agent/orchestrator.py](../app/agent/orchestrator.py))
  was a flat tool-calling loop; the LLM read **raw** search rows.
- `semantic_rank`, `extract_car_info`, and `log_disagreements` were built and
  tested but unreachable from `/chat`.
- The API `cars[]` payload was "everything the search tools returned this
  turn", although [API_CONTRACT.md](API_CONTRACT.md) promised "only cars the
  reply references" — a latent bug that later surfaced live (§8).

## 2. Intelligence Agent refactor

**File:** [app/agent/intelligence_agent.py](../app/agent/intelligence_agent.py)

- Pipeline role formalized: input = retrieved listings (SQL or RAG rows),
  output = structured JSON only. Per vehicle: `id, make, model, trim, year,
  title, description, url, price, body_type`.
- New `url` field, sourced from the row's `photo_url` — always copied from
  the source row, never produced by the LLM (same trust policy as `title`).
- `id` kept in the output (joins results back to inventory; required by
  `cross_reference.log_disagreements`).
- `user_query` made optional (context for description emphasis only).
- Reused as-is: strict-JSON parsing, malformed-entry skipping,
  join-back-by-id grounding.

## 3. Shadow retrieval → intelligence pipeline

**New:** [app/agent/retrieval_pipeline.py](../app/agent/retrieval_pipeline.py) ·
**Modified:** [app/api/routes.py](../app/api/routes.py),
[app/main.py](../app/main.py) ·
**New:** [tests/test_retrieval_pipeline.py](../tests/test_retrieval_pipeline.py),
[app/agent/_pipeline_smoke_test.py](../app/agent/_pipeline_smoke_test.py)

- `gather_candidates` merges the turn's SQL tool results with
  `semantic_rank` matches for the raw user message (SQL first, deduped by
  id, capped at 10); `run_pipeline` extracts and logs the structured JSON;
  `shadow_run` executes it on a daemon thread off the request path.
- `/chat` collects retrieval-tool results via the pre-existing
  `on_tool_call` observer hook and fires the shadow run after the turn —
  only on turns where a retrieval tool actually ran.
- Scoped logging config in `main.py` makes the pipeline's INFO logs visible
  under uvicorn without making anything else noisier.
- Semantic search's failure mode (embeddings not built) degrades to
  SQL-only with a warning; pipeline failures never affect the reply.

## 4. Extraction prompt hardening

**File:** [app/agent/intelligence_agent.py](../app/agent/intelligence_agent.py) (prompt only)

- **Prices:** explicit exclusion list from real listing noise (down
  payments/"0% DP", deposits, salary requirements, report fees, warranty
  amounts, mileage, engine size, horsepower, years); cash preferred over
  monthly; digit-normalization examples matching the dataset (`115,750`,
  `83.000` dot-thousands, `1,813.00`); Arabic monthly marker `شهري`;
  market-grounded sanity ranges (cash ≥ 10,000 AED, monthly 100–20,000 AED —
  the same bounds as the deterministic extractor); null on any uncertainty
  or conflict.
- **Body types:** concrete decision rules (convertible wins over sporty;
  van/pickup/wagon/sports-car-vs-coupe distinctions) with named examples;
  `other` when unsure.
- **Grounding carve-out:** widely known automotive knowledge permitted ONLY
  for `body_type`; every other field exclusively from the listing text.
- **Determinism:** fixed key order, JSON `null` only, same order/length as
  input, no fences/commentary.
- Verified against the deterministic columns: 0 cross-reference
  disagreements on the tricky cases (monthly-only, cash+monthly, no-price,
  convertible M4).

## 5. Orchestrator consumes the Intelligence Agent

**File:** [app/agent/orchestrator.py](../app/agent/orchestrator.py) ·
**New:** [tests/test_orchestrator_intelligence.py](../tests/test_orchestrator_intelligence.py)

The conversational LLM's view of `search_inventory` results is now the
intelligence agent's structured entries (`_intelligence_view`), not raw rows.
Design decisions, each protecting "UX unchanged":

- The intel entry's single `price` is **excluded** from the view — it cannot
  distinguish cash from monthly. The deterministic
  `price_aed_cash`/`price_aed_monthly`/`price_unlisted`/`mileage_km` are
  merged in from the raw row (the system prompt's grounding rules reference
  them).
- `get_car_details` stays raw: its full description grounds detail Q&A
  ("does it have a sunroof?"); a 2-sentence summary would change answers.
- Per-row fallback: rows the agent fails to extract pass through raw, so a
  car can never render as a card while being invisible to the LLM.
- Raw rows still feed the API `cars[]` payload and the observer — card
  contract untouched; frontend untouched.
- Cost note: one extra synchronous Gemini call per turn with a non-empty
  search.

## 6. Full pipeline verification

- 19/19 automated end-to-end checks (throwaway script, deleted after):
  strict-JSON validity, price agreement with deterministic extraction,
  body-type vocabulary, intel-view-only tool messages, all five HTTP
  endpoints incl. 404 paths, shadow pipeline firing (`sql=3, semantic=4`).
- Real-browser verification (frontend dev server + preview): login as
  seeded user, returning-user memory banner, chat turn, car cards with
  correct price display ("AED 165,000" / "Price on request"), zero console
  errors.
- Full unit suite green throughout.

## 7. Dead-code audit (no removals)

Requested cleanup of newly-unused deterministic extraction. Audit conclusion:
**nothing became obsolete** — the deterministic columns became *more*
load-bearing (the intel view deliberately keeps them; cross-reference uses
them as ground truth). Ruff F401/F811/F841/ERA clean; all 26 modules import;
reference-count audit showed live callers for every extraction function.
Flagged but preserved: `is_arabic` (write-only column since Phase 1) and the
shadow pipeline's duplicate extraction (see Known items).

## 8. Card filter — the Ferrari bug

**File:** [app/agent/orchestrator.py](../app/agent/orchestrator.py) ·
**New:** [tests/test_card_filter.py](../tests/test_card_filter.py)

Live bug: a price-capped SUV search returned an unlisted-price 2007 Ferrari
F430 (price-capped searches include unlisted-price rows by design); the
reply never mentioned it; its card rendered anyway.

Fix: `_cars_mentioned_in_reply` keeps only cars the reply actually names —
which is what the API contract always promised. Matching rules:

- Model-name matching is primary: ≥ half a model's distinctive tokens as
  words ("Urban Cruiser" can't claim the Land Cruiser 70 Series), plus a
  separator-tolerant whole-model match for generic-token models (C-Class).
- Make matching only as fallback when the reply names no models at all
  ("I found a few BMWs") — otherwise "our Toyota SUVs" would drag in every
  searched Toyota (observed live before tightening).
- The `booking_prompt` car is always kept.
- Dropped ids logged: `cards filtered to reply mentions: kept=... dropped=...`

## 9. Card ordering + variant-code matching

**File:** [app/agent/orchestrator.py](../app/agent/orchestrator.py) ·
**Tests:** [tests/test_card_filter.py](../tests/test_card_filter.py)

- Cards now render **in the order the reply first names each car**
  (`_model_mention_pos` records match positions; cards sort by them), not in
  inventory-id order. An unnamed booking-prompt car leads.
- Variant-code matching added: cars stored under family model names
  (`3-series`, `v-class`) are recognized by variant codes from their listing
  titles (`330i`, `V220d`, `F430` — 3+ chars mixing letters and digits),
  with a stoplist for shared spec jargon (`4MATIC`, `4WD`, `0KM`, `V6`…) so
  one car's "4MATIC" can't vouch for another. Without this, the 330i and
  V220d cards in the motivating example would have been dropped entirely.

## 10. Booking-widget / reply reconciliation

**File:** [app/agent/orchestrator.py](../app/agent/orchestrator.py) ·
**New:** [tests/test_booking_reconcile.py](../tests/test_booking_reconcile.py)

Live bug: the model proposed 20:00 via `propose_booking` (valid — exactly
closing time), then wrote "Thursday at 7:30 PM instead" without re-proposing.
The widget asked "Thursday at 20:00?" while the text offered 19:30.

Fix: `_reconcile_booking_prompt` runs on every final reply:

1. Reply mentions the proposed slot (any phrasing; "2:00 PM" ≡ "14:00") → keep.
2. Reply names exactly one *different* slot that re-validates through
   `check_availability` → widget corrected to follow the text.
3. Ambiguous or invalid → widget dropped (logged); plain-text yes/no still works.
4. Reply names no slot → keep (nothing to contradict).

Slot parser: both word orders, 12h/24h forms; the day↔time gap allows no
digits and no sentence ends, so days pair only with adjacent times ("we
close at 8:00 PM" contributes nothing; "Monday at 08:00, then 9:30 pm on
Tuesday" pairs each day with its own time).

---

## Files touched this session

| File | Change |
|---|---|
| [app/agent/intelligence_agent.py](../app/agent/intelligence_agent.py) | Refactor to pipeline contract; `url` field; hardened prompt |
| [app/agent/orchestrator.py](../app/agent/orchestrator.py) | Intel view for search results; card mention-filter + ordering; booking reconciliation |
| [app/agent/retrieval_pipeline.py](../app/agent/retrieval_pipeline.py) | **New** — shadow SQL+semantic → extraction → JSON logging |
| [app/api/routes.py](../app/api/routes.py) | Shadow-run wiring via `on_tool_call` observer (shapes unchanged) |
| [app/main.py](../app/main.py) | Scoped INFO logging for the pipeline logger |
| [app/agent/_pipeline_smoke_test.py](../app/agent/_pipeline_smoke_test.py) | **New** — manual smoke script |
| [tests/test_retrieval_pipeline.py](../tests/test_retrieval_pipeline.py) | **New** — 4 tests |
| [tests/test_orchestrator_intelligence.py](../tests/test_orchestrator_intelligence.py) | **New** — 3 tests |
| [tests/test_card_filter.py](../tests/test_card_filter.py) | **New** — 12 tests (incl. live-bug reproductions) |
| [tests/test_booking_reconcile.py](../tests/test_booking_reconcile.py) | **New** — 10 tests (incl. live-bug reproduction) |

Untouched by design: system prompt, tool schemas/implementations, retrieval
modules, DB schema, API contract shapes, frontend. Test suite: 45 → **74
passing** (13 integration tests remain opt-in).

## Known items / next steps

1. **Semantic search is still not in the live conversational path** — it
   runs only in the shadow pipeline. Promoting it (e.g. as an orchestrator
   tool or merged into search results pre-extraction) is the main remaining
   step toward the target flow diagram.
2. **Double extraction per search turn** — orchestrator and shadow pipeline
   each call `extract_car_info`. Consolidate or remove the shadow run when
   item 1 is decided. Matters for the Gemini free tier (15 req/min).
3. **Temperature not pinned** in [app/agent/llm_client.py](../app/agent/llm_client.py) —
   extraction wording can vary run-to-run.
4. Pre-existing model flakiness (plain-text slot offers without
   `propose_booking`, per [BUG_AUDIT.md](BUG_AUDIT.md)) — now *contained* by
   the reconciliation layer rather than fixed at the prompt level.
5. Card/booking matchers are deterministic text heuristics; the structural
   fix (conversational agent declares car ids explicitly) belongs to the
   future multi-agent pipeline.
6. **The repo has no git commits yet** — all work is untracked. Commit
   before submission.
