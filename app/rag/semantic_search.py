"""Semantic ranking over inventory descriptions — local embeddings + numpy.

At 100 rows a brute-force cosine over all stored vectors is instant; no
vector DB needed.
"""

import json
from functools import lru_cache
from typing import Any

import numpy as np
import sqlalchemy as sa

from app.db.database import get_connection
from app.db.schema import inventory, inventory_embeddings

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model():
    from fastembed import TextEmbedding  # deferred: slow import, model load

    return TextEmbedding(EMBEDDING_MODEL_NAME)


def _load_matrix() -> tuple[list[int], np.ndarray]:
    with get_connection() as conn:
        rows = conn.execute(
            sa.select(
                inventory_embeddings.c.car_id, inventory_embeddings.c.embedding_json
            ).order_by(inventory_embeddings.c.car_id)
        ).all()
    if not rows:
        raise RuntimeError(
            "No embeddings found — run `uv run python -m app.rag.embed_inventory` first"
        )
    ids = [r[0] for r in rows]
    matrix = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
    return ids, matrix


def semantic_rank(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Top-`limit` inventory rows by cosine similarity of description_clean to
    `query`. Each dict is the full inventory row plus `similarity`."""
    ids, matrix = _load_matrix()
    q = np.array(next(iter(get_model().embed([query]))), dtype=np.float32)

    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(q)
    norms[norms == 0] = 1e-12
    sims = (matrix @ q) / norms

    order = np.argsort(-sims)[:limit]
    top = {ids[i]: float(sims[i]) for i in order}

    with get_connection() as conn:
        rows = conn.execute(
            sa.select(inventory).where(inventory.c.id.in_(top))
        ).mappings().all()
    by_id = {r["id"]: dict(r) for r in rows}

    results = []
    for i in order:
        car_id = ids[i]
        if car_id in by_id:  # embedding may be stale if inventory was rebuilt
            row = by_id[car_id]
            row["similarity"] = top[car_id]
            results.append(row)
    return results
