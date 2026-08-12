"""
Database setup.

Uses DATABASE_URL if set (Railway's Postgres plugin provides this in
production). Falls back to a local SQLite file for zero-friction local
dev when DATABASE_URL isn't set.
"""

import os

from sqlalchemy import BigInteger, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

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


def _ensure_columns() -> None:
    """
    Additive-only, no-framework migration: create_all() only creates
    missing tables, not missing columns on tables that already exist
    (e.g. the live Postgres 'summaries' table gaining new fields). Also
    widens INTEGER columns to BIGINT when the model has moved to
    BigInteger (e.g. view_count overflowing a 32-bit int on viral videos)
    — widening never loses data, so it's safe to run automatically too.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            existing_columns = {col["name"]: col for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing_columns:
                    col_type = column.type.compile(dialect=engine.dialect)
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))
                    continue
                existing_type = str(existing_columns[column.name]["type"]).upper()
                if (
                    engine.dialect.name == "postgresql"
                    and isinstance(column.type, BigInteger)
                    and existing_type == "INTEGER"
                ):
                    conn.execute(text(f"ALTER TABLE {table.name} ALTER COLUMN {column.name} TYPE BIGINT"))


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
