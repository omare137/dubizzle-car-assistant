"""Shadow retrieval → intelligence pipeline.

Bridges the retrieval layers that already exist — the SQL rows the live
agent's tools returned this turn (app/tools/inventory_search.py) plus local
semantic search over descriptions (app/rag/semantic_search.py) — into the
intelligence agent, and logs the structured JSON it produces for inspection.

Observational only: the API layer fires it off the /chat request path on a
daemon thread. It never alters replies, tool results, or API shapes; failures
are logged and swallowed.
"""

import json
import logging
import threading
from typing import Any

from app.agent.intelligence_agent import extract_car_info
from app.rag.semantic_search import semantic_rank

logger = logging.getLogger(__name__)

# Top-k semantic complements per query — small, so the extraction batch stays
# focused on what the user actually asked about.
SEMANTIC_LIMIT = 5
# Cap on the merged batch sent to the intelligence agent (one LLM call).
MAX_CANDIDATES = 10


def gather_candidates(user_query: str, sql_rows: list[dict]) -> list[dict[str, Any]]:
    """Merge the turn's SQL-retrieved rows with semantic matches for the raw
    user query. SQL rows come first (they matched explicit filters); semantic
    rows only fill the remaining slots. Deduped by inventory id."""
    merged: dict[int, dict[str, Any]] = {}
    for row in sql_rows:
        merged.setdefault(row["id"], row)
    try:
        semantic_rows = semantic_rank(user_query, limit=SEMANTIC_LIMIT)
    except Exception:
        # e.g. embeddings not built yet (app.rag.embed_inventory) — the SQL
        # half of the pipeline still works on its own.
        logger.warning("semantic search unavailable; using SQL candidates only", exc_info=True)
        semantic_rows = []
    for row in semantic_rows:
        merged.setdefault(row["id"], row)
    return list(merged.values())[:MAX_CANDIDATES]


def run_pipeline(user_query: str, sql_rows: list[dict]) -> list[dict[str, Any]]:
    """Gather candidates, run the intelligence agent over them, and log the
    structured JSON. Returns the extracted list (the later multi-agent
    pipeline will consume this; today it is logged for inspection)."""
    candidates = gather_candidates(user_query, sql_rows)
    if not candidates:
        logger.info("intelligence pipeline: no candidates for query %r", user_query)
        return []
    sql_ids = {row["id"] for row in sql_rows}
    n_sql = sum(1 for c in candidates if c["id"] in sql_ids)
    results = extract_car_info(candidates, user_query)
    logger.info(
        "intelligence pipeline: query=%r candidates=%d (sql=%d, semantic=%d) -> %d extracted\n%s",
        user_query,
        len(candidates),
        n_sql,
        len(candidates) - n_sql,
        len(results),
        json.dumps(results, indent=2, ensure_ascii=False),
    )
    return results


def shadow_run(user_query: str, sql_rows: list[dict]) -> None:
    """Fire-and-forget run_pipeline on a daemon thread, keeping the extra
    embedding + LLM work off the /chat request path."""

    def _target() -> None:
        try:
            run_pipeline(user_query, sql_rows)
        except Exception:
            logger.exception("intelligence pipeline failed (non-blocking)")

    threading.Thread(target=_target, name="intelligence-pipeline", daemon=True).start()
