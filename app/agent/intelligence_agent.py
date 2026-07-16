"""Intelligence agent: LLM-based structured extraction over retrieved listings.

Pipeline role: takes candidate rows already retrieved by SQL
(app/tools/inventory_search.py) or RAG (app/rag/semantic_search.py) and
returns structured JSON only — one object per vehicle with make, model, trim,
year, title, description, url, price and body_type (plus the inventory id, so
results can be joined back / cross-referenced).

Not wired into the live chat orchestrator yet. Part C cross-references its
output against the deterministic Phase 1 columns (app/rag/cross_reference.py).
"""

import json
import logging
import re
from typing import Any

from app.agent.llm_client import get_completion

logger = logging.getLogger(__name__)

BODY_TYPES = (
    "suv", "sedan", "hatchback", "coupe", "pickup_truck",
    "sports_car", "van_minivan", "convertible", "wagon", "other",
)

EXTRACTION_PROMPT = f"""\
You are a deterministic data extraction engine for used-car listings from a
UAE marketplace. Input: a JSON array of candidate listings (fields: id, make,
model, trim, year, title, description_clean). For EACH candidate, output one
JSON object with EXACTLY these keys, in this order:
"id", "make", "model", "trim", "year", "title", "description", "price",
"body_type".

Grounding:
- You may use widely known automotive knowledge (e.g. common make/model/body
  type relationships — a Hilux is a pickup, a V-Class is a van) ONLY when
  judging "body_type".
- Every other field (copied fields, "description", "price") must rely
  exclusively on the supplied listing information. Never invent or fill in
  missing values from general knowledge.

Copied fields:
- "id", "make", "model", "trim", "year", "title": copy VERBATIM from the
  input — no re-casing, no translation, no correction, no substitution. "id"
  is required; it joins results back to the inventory.

Description ("description"):
- 1-2 plain English sentences summarizing that listing's own title and
  description_clean (English even when the listing text is Arabic).
- Use ONLY facts stated in that listing. Never add features, condition,
  history, or specs it does not state; never carry facts over from another
  candidate or from the user query; no marketing language.
- If the listing text is thin, summarize the little that is stated rather
  than padding or inventing.

Price ("price" — bare JSON integer in AED, or null):
- Use a number ONLY if the listing itself states it as the vehicle's price.
- Prefer the full/cash price. If ONLY a monthly instalment is stated (e.g.
  "1,430 AED/month", "3,819 P.M", "شهري"), use that monthly amount.
- NEVER treat these as the price: down payments ("0% DP", deposit), salary
  requirements, inspection/report fees, warranty or service amounts, mileage,
  engine size, horsepower, or years.
- Normalize digits only: strip currency words, commas, spaces, and thousands
  separators ("115,750" -> 115750; "83.000" -> 83000; "1,813.00" -> 1813).
  No strings, no decimals, no separators in the output.
- Sanity check: cash prices in this market are >= 10000 AED; monthly
  instalments are between 100 and 20000 AED. If a candidate number falls
  outside these ranges, or you are not confident it is the vehicle's price,
  or two conflicting cash prices appear — output null. Null is always the
  correct answer when uncertain; a guessed price is never acceptable.

Body type ("body_type" — exactly one of {json.dumps(list(BODY_TYPES))}):
- Judge from the model and the listing text together.
- "convertible" wins whenever the listing says convertible/cabriolet, even
  for sporty models. Vans and people-movers (e.g. V-Class, Carnival) are
  "van_minivan". Pickups (e.g. Hilux, F-150) are "pickup_truck". Estates are
  "wagon". Two-door performance cars (e.g. 911, M4) are "sports_car" unless
  convertible; ordinary two-doors are "coupe".
- If genuinely unsure between categories, output "other" — never guess.

Output format (strict):
- ONLY a JSON array — no markdown fences, no commentary, no trailing commas.
- Same order and same LENGTH as the input array; each input id appears
  exactly once.
- Every object has all 9 keys. Missing/unknown values are JSON null — never
  the string "null", "N/A", 0, or an empty string.
- The same input must always produce the same output.

The user query, when given, is context for description emphasis only — it is
NEVER a source of facts, prices, or corrections.
"""


def _parse_json_list(text: str) -> list | None:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Intelligence agent returned unparseable JSON: %.200s", text)
        return None
    return data if isinstance(data, list) else None


def extract_car_info(
    candidate_rows: list[dict], user_query: str = ""
) -> list[dict[str, Any]]:
    """LLM-extract structured info for each retrieved row (SQL or RAG shape —
    any dict with the inventory columns; extra keys like `similarity` are
    ignored). Malformed entries are logged and skipped, never crash the batch.

    Returns one dict per vehicle: id, make, model, trim, year, title,
    description, url, price, body_type.
    """
    if not candidate_rows:
        return []
    by_id = {row["id"]: row for row in candidate_rows}
    payload = [
        {
            "id": row["id"],
            "make": row["make"],
            "model": row["model"],
            "trim": row["trim"],
            "year": row["year"],
            "title": row["title"],
            "description_clean": row["description_clean"],
        }
        for row in candidate_rows
    ]
    context = f"User query (context only): {user_query}\n\n" if user_query else ""
    response = get_completion(
        [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": f"{context}Candidates:\n{json.dumps(payload, ensure_ascii=False)}",
            },
        ]
    )
    parsed = _parse_json_list(response.choices[0].message.content or "")
    if parsed is None:
        return []

    results: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict) or entry.get("id") not in by_id:
            logger.warning("Skipping malformed agent entry: %.150s", str(entry))
            continue
        source = by_id[entry["id"]]
        price = entry.get("price")
        body_type = entry.get("body_type")
        results.append(
            {
                "id": source["id"],
                "make": entry.get("make") or source["make"],
                "model": entry.get("model") or source["model"],
                "trim": entry.get("trim") or source["trim"],
                "year": source["year"],
                "title": source["title"],  # from source: never trust re-typed copies
                "description": entry.get("description") or source["description_clean"],
                "url": source["photo_url"],  # from source, not the LLM
                "price": int(price) if isinstance(price, (int, float)) else None,
                "body_type": body_type if body_type in BODY_TYPES else "other",
            }
        )
    return results
