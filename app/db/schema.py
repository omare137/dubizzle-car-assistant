"""SQLAlchemy Core table definitions — single source of truth for the schema.

`inventory` is populated by app/db/build_inventory.py; the rest are created
and seeded by app/db/init_db.py.
"""

from datetime import datetime, timezone

import sqlalchemy as sa


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


metadata = sa.MetaData()

inventory = sa.Table(
    "inventory",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("make", sa.String, nullable=False),
    sa.Column("model", sa.String, nullable=False),
    sa.Column("trim", sa.String, nullable=False),
    sa.Column("year", sa.Integer, nullable=False),
    sa.Column("title", sa.String, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("description_clean", sa.Text, nullable=False),
    sa.Column("is_arabic", sa.Boolean, nullable=False),
    sa.Column("price_aed_cash", sa.Integer, nullable=True),
    sa.Column("price_aed_monthly", sa.Integer, nullable=True),
    sa.Column("mileage_km", sa.Integer, nullable=True),
    sa.Column("photo_url", sa.String, nullable=False),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("username", sa.String, unique=True, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    # Stored only, never used in recommendation logic.
    sa.Column("gender", sa.String, nullable=True),
    # Stored only; inventory currently has no location data to match against.
    sa.Column("address", sa.String, nullable=True),
    sa.Column("created_at", sa.DateTime, nullable=False, default=utcnow),
)

# Long-term memory: one row per user, updated over time.
profiles = sa.Table(
    "profiles",
    metadata,
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
    # JSON blob: budget range, preferred make/model/body type, engaged
    # inventory ids, free-text notes from the summarizer.
    sa.Column("preferences_json", sa.Text, nullable=True),
    sa.Column("last_updated", sa.DateTime, nullable=False, default=utcnow),
)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.String, primary_key=True),  # uuid
    # Nullable: anonymous/guest sessions are allowed.
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    sa.Column("started_at", sa.DateTime, nullable=False, default=utcnow),
    sa.Column("ended_at", sa.DateTime, nullable=True),
)

# Short-term memory: full transcript per session.
messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("session_id", sa.String, sa.ForeignKey("sessions.id"), nullable=False),
    sa.Column("role", sa.String, nullable=False),  # "user" | "assistant" | "tool"
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False, default=utcnow),
)

# Semantic-search embeddings, one row per inventory row. Separate table (not a
# column on inventory) because build_inventory.py drops/recreates inventory on
# every rebuild; embeddings survive and are re-synced by embed_inventory.py.
# No FK on car_id for the same reason (the parent table is transient).
inventory_embeddings = sa.Table(
    "inventory_embeddings",
    metadata,
    sa.Column("car_id", sa.Integer, primary_key=True),  # == inventory.id
    sa.Column("model_name", sa.String, nullable=False),
    sa.Column("embedding_json", sa.Text, nullable=False),  # JSON float array
    sa.Column("created_at", sa.DateTime, nullable=False, default=utcnow),
)

# Observational log: cases where the LLM intelligence agent's extraction
# disagrees with the deterministic Phase 1 extraction for the same car.
extraction_disagreements = sa.Table(
    "extraction_disagreements",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("car_id", sa.Integer, nullable=False),
    sa.Column("timestamp", sa.DateTime, nullable=False, default=utcnow),
    sa.Column("deterministic_price_cash", sa.Integer, nullable=True),
    sa.Column("deterministic_price_monthly", sa.Integer, nullable=True),
    sa.Column("deterministic_body_type", sa.String, nullable=True),  # pending: no classify_body_type column yet
    sa.Column("agent_price", sa.Integer, nullable=True),
    sa.Column("agent_body_type", sa.String, nullable=True),
    sa.Column("session_id", sa.String, nullable=True),
    sa.Column("user_query", sa.Text, nullable=True),
)

leads = sa.Table(
    "leads",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    sa.Column("session_id", sa.String, sa.ForeignKey("sessions.id"), nullable=False),
    # Nullable: a lead can be general budget/needs capture with no car picked.
    sa.Column("car_id", sa.Integer, sa.ForeignKey("inventory.id"), nullable=True),
    sa.Column("budget_range", sa.String, nullable=True),
    sa.Column("needs_notes", sa.Text, nullable=True),
    sa.Column("booking_day", sa.String, nullable=True),  # e.g. "Wednesday"
    sa.Column("booking_time", sa.String, nullable=True),  # e.g. "14:00"
    # "captured" | "booking_proposed" | "booking_confirmed"
    sa.Column("status", sa.String, nullable=False, default="captured"),
    sa.Column("created_at", sa.DateTime, nullable=False, default=utcnow),
)
