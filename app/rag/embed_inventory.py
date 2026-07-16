"""One-time batch: embed every inventory row's description_clean locally.

Usage: uv run python -m app.rag.embed_inventory [--force]

Uses all-MiniLM-L6-v2 via fastembed (ONNX, fully local — no API calls, no
tokens). Idempotent: rows that already have an embedding are skipped unless
--force is passed.
"""

import json
import sys
import time

import sqlalchemy as sa

from app.db.database import engine, get_connection
from app.db.schema import inventory, inventory_embeddings, metadata, utcnow
from app.rag.semantic_search import EMBEDDING_MODEL_NAME, get_model


def embed_inventory(force: bool = False) -> int:
    metadata.create_all(engine, tables=[inventory_embeddings])
    with get_connection() as conn:
        if force:
            conn.execute(inventory_embeddings.delete())
        done_ids = {
            row[0] for row in conn.execute(sa.select(inventory_embeddings.c.car_id))
        }
        rows = conn.execute(
            sa.select(inventory.c.id, inventory.c.description_clean).order_by(inventory.c.id)
        ).all()

    todo = [(cid, text) for cid, text in rows if cid not in done_ids]
    if not todo:
        print(f"All {len(rows)} rows already embedded — nothing to do (use --force to redo)")
        return 0

    start = time.perf_counter()
    model = get_model()
    vectors = list(model.embed([text for _, text in todo]))
    elapsed = time.perf_counter() - start

    with get_connection() as conn:
        conn.execute(
            inventory_embeddings.insert(),
            [
                {
                    "car_id": cid,
                    "model_name": EMBEDDING_MODEL_NAME,
                    "embedding_json": json.dumps([float(x) for x in vec]),
                    "created_at": utcnow(),
                }
                for (cid, _), vec in zip(todo, vectors)
            ],
        )
    print(
        f"Embedded {len(todo)} rows (dim={len(vectors[0])}) in {elapsed:.2f}s "
        f"({len(done_ids)} already present, {len(rows)} total)"
    )
    return len(todo)


if __name__ == "__main__":
    embed_inventory(force="--force" in sys.argv)
