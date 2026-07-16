"""Create the app tables and seed mock users.

Usage: uv run python -m app.db.init_db

Idempotent: create_all skips existing tables, and seed users are only
inserted when the users table is empty.
"""

import sqlalchemy as sa

from app.db.database import engine, get_connection
from app.db.schema import metadata, users

SEED_USERS = [
    {"username": "omar.k", "name": "Omar Khalid", "gender": "male", "address": "Al Barsha, Dubai"},
    {"username": "sara.m", "name": "Sara Mansour", "gender": "female", "address": "Khalifa City, Abu Dhabi"},
    {"username": "james.w", "name": "James Walker", "gender": None, "address": "Dubai Marina, Dubai"},
]


def init_db() -> None:
    metadata.create_all(engine)
    with get_connection() as conn:
        n_users = conn.execute(sa.select(sa.func.count()).select_from(users)).scalar_one()
        if n_users == 0:
            conn.execute(users.insert(), SEED_USERS)
            print(f"Seeded {len(SEED_USERS)} mock users")
        else:
            print(f"users table already has {n_users} rows — skipping seed")


if __name__ == "__main__":
    init_db()
    print("Tables ready:", ", ".join(sorted(metadata.tables)))
