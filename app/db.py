"""
Database setup.

Uses DATABASE_URL if set (Railway's Postgres plugin provides this in
production). Falls back to a local SQLite file for zero-friction local
dev when DATABASE_URL isn't set.
"""

import logging
import os
import re

from sqlalchemy import BigInteger, String, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

_VARCHAR_LEN_RE = re.compile(r"^VARCHAR\((\d+)\)$")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

# Railway (and most Postgres hosts) hand out "postgres://", but SQLAlchemy's
# psycopg3 dialect wants "postgresql+psycopg://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _run_migration(sql: str) -> None:
    """
    Run one migration statement in its own transaction, logging and
    swallowing failures instead of letting them propagate. These run at
    startup, so an exception here would take the entire app down — a
    single column that can't be altered should degrade to "that one
    feature misbehaves", not "the site is offline". Each statement gets
    its own transaction so one failure can't roll back the others.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception:
        logger.exception("Auto-migration statement failed (continuing): %s", sql)


def _ensure_columns() -> None:
    """
    Additive-only, no-framework migration: create_all() only creates
    missing tables, not missing columns on tables that already exist
    (e.g. the live Postgres 'summaries' table gaining new fields). Also
    widens columns when the model has grown them — INTEGER to BIGINT
    (view_count overflowing a 32-bit int on viral videos) and VARCHAR(n)
    to a longer n (category holding a comma-joined label list). Widening
    never loses data, so it's safe to run automatically too.
    """
    inspector = inspect(engine)
    for table in Base.metadata.tables.values():
        existing_columns = {col["name"]: col for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing_columns:
                col_type = column.type.compile(dialect=engine.dialect)
                _run_migration(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}")
                continue
            existing_type = str(existing_columns[column.name]["type"]).upper()
            if engine.dialect.name != "postgresql":
                continue
            if isinstance(column.type, BigInteger) and existing_type == "INTEGER":
                _run_migration(f"ALTER TABLE {table.name} ALTER COLUMN {column.name} TYPE BIGINT")
                continue
            # Widen VARCHAR(n) when the model asks for a longer n. Only ever
            # grows — a shorter model length is ignored rather than
            # truncating live rows.
            if isinstance(column.type, String) and column.type.length:
                match = _VARCHAR_LEN_RE.match(existing_type)
                if match and int(match.group(1)) < column.type.length:
                    _run_migration(
                        f"ALTER TABLE {table.name} ALTER COLUMN {column.name} "
                        f"TYPE VARCHAR({column.type.length})"
                    )


def init_db() -> None:
    from app import models  # noqa: F401 — register models on Base before create_all

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
