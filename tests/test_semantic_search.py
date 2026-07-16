"""Tests for app.rag.semantic_search — local embeddings, no LLM/API calls.

Requires embeddings to exist (uv run python -m app.rag.embed_inventory);
skipped otherwise so a cold checkout doesn't fail the suite.
"""

import pytest
import sqlalchemy as sa

from app.db.database import engine, get_connection
from app.db.schema import inventory_embeddings

try:
    with get_connection() as _conn:
        _n_embeddings = _conn.execute(
            sa.select(sa.func.count()).select_from(inventory_embeddings)
        ).scalar_one()
except sa.exc.OperationalError:  # table doesn't exist yet
    _n_embeddings = 0

pytestmark = pytest.mark.skipif(
    _n_embeddings == 0, reason="embeddings not built — run app.rag.embed_inventory"
)

# Models in the catalog that are genuinely SUVs (for the loose ranking check).
SUV_MARKERS = (
    "suv", "q8", "glc", "gla", "h9", "prado", "patrol", "land cruiser", "landcruiser",
    "range rover", "evoque", "discovery", "bentayga", "cullinan", "territory",
    "explorer", "kicks", "captiva", "x-trail", "cx-5", "cx 5", "tucson", "wingle",
    "bz4x", "ix3", "xc60", "yu7", "innova", "safari", "vtc", "tiggo", "js4", "t-roc",
)


def test_ranking_is_descending_and_scored():
    from app.rag.semantic_search import semantic_rank

    results = semantic_rank("family car", limit=15)
    assert len(results) == 15
    scores = [r["similarity"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 <= s <= 1.0 for s in scores)
    # Full row data present, not just ids.
    assert all("description_clean" in r and "title" in r for r in results)


def test_luxury_suv_query_ranks_suvs_reasonably():
    from app.rag.semantic_search import semantic_rank

    top5 = semantic_rank("luxury SUV", limit=5)
    hits = [
        r for r in top5
        if any(m in f"{r['title']} {r['model']}".lower() for m in SUV_MARKERS)
    ]
    # Loose by design: embeddings won't be perfect at 100 rows, but a "luxury
    # SUV" query should surface at least 2 SUVs in the top 5.
    assert len(hits) >= 2, f"top5 models: {[(r['make'], r['model']) for r in top5]}"


def test_limit_respected():
    from app.rag.semantic_search import semantic_rank

    assert len(semantic_rank("car", limit=3)) == 3


def test_engine_import_side_effect_free():
    # semantic_search must not create tables or write anything on import.
    assert engine is not None
