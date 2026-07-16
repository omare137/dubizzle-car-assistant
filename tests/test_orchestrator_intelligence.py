"""Tests for the orchestrator's intelligence view of search results —
extract_car_info is mocked, no LLM calls."""

from app.agent import orchestrator as orch
from app.tools import inventory_search as inv


def intel_entry(row: dict, description: str = "summary") -> dict:
    """An intelligence-agent output entry for `row` (schema per
    app/agent/intelligence_agent.extract_car_info)."""
    return {
        "id": row["id"],
        "make": row["make"],
        "model": row["model"],
        "trim": row["trim"],
        "year": row["year"],
        "title": row["title"],
        "description": description,
        "url": row["photo_url"],
        "price": 123,  # deliberately wrong: must never reach the view
        "body_type": "suv",
    }


def test_view_swaps_in_intel_entries_and_keeps_price_split(monkeypatch):
    rows = inv.search_inventory(make="bmw", limit=2)
    monkeypatch.setattr(orch, "extract_car_info", lambda r, q="": [intel_entry(x) for x in r])

    view = orch._intelligence_view(rows, "bmws under 200k")

    assert [v["id"] for v in view] == [r["id"] for r in rows]
    for v, r in zip(view, rows):
        assert v["description"] == "summary"
        assert v["body_type"] == "suv"
        # Deterministic price semantics preserved for the grounding rules.
        assert v["price_aed_cash"] == r["price_aed_cash"]
        assert v["price_aed_monthly"] == r["price_aed_monthly"]
        assert v["price_unlisted"] == r["price_unlisted"]
        assert v["mileage_km"] == r["mileage_km"]
        # The ambiguous single price and the raw long text never appear.
        assert "price" not in v
        assert "description_clean" not in v


def test_view_falls_back_per_row_when_entry_missing(monkeypatch):
    rows = inv.search_inventory(make="bmw", limit=2)
    # Agent only returns an entry for the first row (e.g. malformed second).
    monkeypatch.setattr(orch, "extract_car_info", lambda r, q="": [intel_entry(r[0])])

    view = orch._intelligence_view(rows, "")

    assert view[0]["description"] == "summary"
    assert view[1] == rows[1]  # raw fallback — the car never disappears


def test_view_falls_back_wholesale_on_extraction_error(monkeypatch):
    rows = inv.search_inventory(make="bmw", limit=1)

    def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(orch, "extract_car_info", boom)
    assert orch._intelligence_view(rows, "") == rows
