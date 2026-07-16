"""Throwaway manual smoke test for the retrieval → intelligence pipeline.

Run: uv run python -m app.agent._pipeline_smoke_test
Not part of the pytest suite — runs one real local-embedding pass plus one
Gemini extraction call and prints the structured JSON the pipeline logs.
"""

import logging

from app.agent.retrieval_pipeline import run_pipeline
from app.tools import inventory_search as inv


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    query = "a family SUV with a sunroof under 200k"
    sql_rows = inv.search_inventory(price_max=200_000, keywords=["sunroof", "SUV"], limit=5)
    print(f"SQL candidates: {[r['id'] for r in sql_rows]}")

    results = run_pipeline(query, sql_rows)
    print(f"\nExtracted {len(results)} vehicles; ids: {[r['id'] for r in results]}")


if __name__ == "__main__":
    main()
