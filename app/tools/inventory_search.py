"""Inventory search tools for the agent.

These functions only filter and return real rows from the `inventory` table —
they never fabricate, infer, or fall back to data that isn't in the database.
"""

from typing import Any

import sqlalchemy as sa

from app.db.database import get_connection
from app.db.schema import inventory

DESCRIPTION_TRUNCATE_CHARS = 500

RESULT_COLUMNS = (
    "id", "make", "model", "trim", "year", "title", "description_clean",
    "price_aed_cash", "price_aed_monthly", "mileage_km", "photo_url",
)


def _to_result(row: sa.RowMapping, truncate_description: bool) -> dict[str, Any]:
    result = {col: row[col] for col in RESULT_COLUMNS}
    if truncate_description and len(result["description_clean"]) > DESCRIPTION_TRUNCATE_CHARS:
        result["description_clean"] = result["description_clean"][:DESCRIPTION_TRUNCATE_CHARS] + "…"
    # Extraction from the raw listings is best-effort, so a null price means
    # "not listed / not extracted", never "free".
    result["price_unlisted"] = row["price_aed_cash"] is None
    return result


def search_inventory(
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_max: int | None = None,
    keywords: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Filter the inventory table and return matching rows as dicts.

    - `make` / `model`: case-insensitive partial match (SQL LIKE).
    - `year_min` / `year_max`: inclusive range on `year`.
    - `keywords`: list of free-text terms OR-matched (case-insensitive LIKE)
      against `description_clean`. Baseline keyword search — no vector DB.
    - `limit`: max rows returned; results are ordered by id so output is
      deterministic.

    `price_max` — deliberate design choice, not a bug: only ~a third of
    listings have an extractable cash price, so filtering strictly on
    `price_aed_cash <= price_max` would silently hide most of the catalog.
    Rows whose price is null are therefore INCLUDED in price-capped searches
    and flagged with `price_unlisted: True`, so the agent can tell the user
    "price not listed, ask the dealer" instead of the car never appearing.
    Rows with a real price above `price_max` are excluded as expected.

    Returns [] when nothing matches — never a fallback or invented result.
    Each dict's `description_clean` is truncated to ~500 chars; use
    `get_car_details` for the full text.

    Duplicate-listing dedup: the source dataset contains genuine
    duplicate/reposted ads — same (make, model, year, title) under different
    ids (e.g. one Audi Q8 listed 5 times). Results are deduped on that
    composite key, keeping the lowest id. This is deliberately
    presentation-level: the DB keeps every row as a faithful copy of the raw
    listings, and `get_car_details` can still fetch any id directly.
    """
    conditions = []
    if make:
        conditions.append(inventory.c.make.ilike(f"%{make}%"))
    if model:
        conditions.append(inventory.c.model.ilike(f"%{model}%"))
    if year_min is not None:
        conditions.append(inventory.c.year >= year_min)
    if year_max is not None:
        conditions.append(inventory.c.year <= year_max)
    if price_max is not None:
        conditions.append(
            sa.or_(
                inventory.c.price_aed_cash.is_(None),
                inventory.c.price_aed_cash <= price_max,
            )
        )
    if keywords:
        conditions.append(
            sa.or_(
                *[inventory.c.description_clean.ilike(f"%{kw}%") for kw in keywords]
            )
        )

    # No SQL LIMIT: dedup must run before the limit or a duplicate group
    # would shrink the page below `limit` while distinct matches remain.
    query = sa.select(inventory).where(*conditions).order_by(inventory.c.id)
    with get_connection() as conn:
        rows = conn.execute(query).mappings().all()

    seen_keys: set[tuple] = set()
    results: list[dict[str, Any]] = []
    for row in rows:  # ordered by id, so the first occurrence is the lowest id
        key = (row["make"], row["model"], row["year"], row["title"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        results.append(_to_result(row, truncate_description=True))
        if len(results) >= limit:
            break
    return results


def get_car_details(car_id: int) -> dict[str, Any] | None:
    """Return the full row (untruncated description_clean) for one car, or None."""
    query = sa.select(inventory).where(inventory.c.id == car_id)
    with get_connection() as conn:
        row = conn.execute(query).mappings().one_or_none()
    return None if row is None else _to_result(row, truncate_description=False)


def list_distinct_makes() -> list[str]:
    """Sorted distinct `make` values actually present in the catalog.

    Lets the agent ground itself on what exists (and lets guardrail tests
    catch typo variants in the source data, e.g. 'tova' vs 'toyota').
    """
    query = sa.select(inventory.c.make).distinct().order_by(inventory.c.make)
    with get_connection() as conn:
        return [row[0] for row in conn.execute(query)]
