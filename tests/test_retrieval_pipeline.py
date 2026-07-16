"""Tests for app.agent.retrieval_pipeline — no LLM calls (extraction mocked).

Uses the real inventory DB and real local embeddings, same assumptions as
tests/test_semantic_search.py.
"""

import logging

from app.agent import retrieval_pipeline as rp
from app.tools import inventory_search as inv

AGENT_INPUT_COLUMNS = {
    "id", "make", "model", "trim", "year", "title", "description_clean", "photo_url",
}


def test_gather_candidates_puts_sql_rows_first_and_dedupes():
    sql_rows = inv.search_inventory(make="bmw", limit=3)
    out = rp.gather_candidates("sporty german car", sql_rows)

    ids = [r["id"] for r in out]
    assert ids[: len(sql_rows)] == [r["id"] for r in sql_rows]
    assert len(set(ids)) == len(ids)
    assert len(out) <= rp.MAX_CANDIDATES


def test_gather_candidates_semantic_only_when_sql_empty():
    out = rp.gather_candidates("luxury SUV", [])
    assert 0 < len(out) <= rp.SEMANTIC_LIMIT
    # Every candidate row carries the columns the intelligence agent reads.
    for row in out:
        assert AGENT_INPUT_COLUMNS <= set(row)


def test_run_pipeline_extracts_merged_candidates_and_logs(monkeypatch, caplog):
    captured = {}

    def fake_extract(rows, user_query=""):
        captured["rows"] = rows
        captured["user_query"] = user_query
        return [{"id": rows[0]["id"], "make": rows[0]["make"], "url": rows[0]["photo_url"]}]

    monkeypatch.setattr(rp, "extract_car_info", fake_extract)
    sql_rows = inv.search_inventory(make="bmw", limit=2)

    with caplog.at_level(logging.INFO, logger=rp.__name__):
        results = rp.run_pipeline("show me BMWs", sql_rows)

    assert captured["user_query"] == "show me BMWs"
    assert len(captured["rows"]) >= len(sql_rows)  # SQL + semantic complements
    assert results[0]["id"] == sql_rows[0]["id"]
    # The structured JSON is logged for inspection.
    assert any('"url"' in rec.message for rec in caplog.records)


def test_run_pipeline_no_candidates_skips_extraction(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("extract_car_info must not be called with no candidates")

    monkeypatch.setattr(rp, "extract_car_info", boom)
    monkeypatch.setattr(rp, "semantic_rank", lambda *_a, **_k: [])
    assert rp.run_pipeline("anything", []) == []
