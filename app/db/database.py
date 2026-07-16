"""Shared SQLAlchemy engine and connection helper."""

from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa

from app.config import get_settings

engine = sa.create_engine(f"sqlite:///{get_settings().database_path}")


@sa.event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@contextmanager
def get_connection() -> Iterator[sa.Connection]:
    """Yield a connection; commits on clean exit, rolls back on error."""
    with engine.begin() as conn:
        yield conn
