"""Cross-reference the intelligence agent's extraction against the
deterministic Phase 1 columns; log disagreements for offline review.

Purely observational — never raises, never blocks the caller.
"""

import logging
from typing import Any

import sqlalchemy as sa

from app.db.database import engine, get_connection
from app.db.schema import extraction_disagreements, inventory, metadata

logger = logging.getLogger(__name__)

PRICE_TOLERANCE = 0.01  # 1% — absorbs rounding like "1,813.00" vs 1813


def _prices_agree(agent_price: int | None, cash: int | None, monthly: int | None) -> bool:
    if agent_price is None:
        return cash is None and monthly is None
    for det in (cash, monthly):
        if det is not None and abs(agent_price - det) <= det * PRICE_TOLERANCE:
            return True
    return False


def log_disagreements(
    agent_results: list[dict[str, Any]],
    session_id: str | None = None,
    user_query: str | None = None,
) -> int:
    """Compare each agent-extracted car against its deterministic row; write an
    extraction_disagreements row per mismatch. Returns rows written.

    body_type comparison is PENDING: inventory has no deterministic body-type
    column yet (no classify_body_type phase has run), so agent body_type is
    recorded for review but never triggers a disagreement by itself.
    """
    try:
        metadata.create_all(engine, tables=[extraction_disagreements])
        written = 0
        with get_connection() as conn:
            for car in agent_results:
                det = conn.execute(
                    sa.select(
                        inventory.c.price_aed_cash, inventory.c.price_aed_monthly
                    ).where(inventory.c.id == car["id"])
                ).one_or_none()
                if det is None:
                    continue
                cash, monthly = det
                if _prices_agree(car.get("price"), cash, monthly):
                    continue
                conn.execute(
                    extraction_disagreements.insert().values(
                        car_id=car["id"],
                        deterministic_price_cash=cash,
                        deterministic_price_monthly=monthly,
                        deterministic_body_type=None,  # pending, see docstring
                        agent_price=car.get("price"),
                        agent_body_type=car.get("body_type"),
                        session_id=session_id,
                        user_query=user_query,
                    )
                )
                written += 1
        return written
    except Exception:
        logger.exception("cross-reference logging failed (non-blocking)")
        return 0
