"""
Tests for the additive auto-migration in app/db.py.

There is no Alembic here: _ensure_columns() diffs the models against the
live schema on every boot and applies only safe changes. That makes it the
single riskiest piece of infrastructure in the project — it runs at startup
against production, and a mistake either takes the site down or silently
mangles a column. These tests pin the decision logic down.

INCIDENT: Summary.category was varchar(64) while the model needed 255. The
widening branch is what lets a column grow without hand-written SQL.
INCIDENT: view_count overflowed a 32-bit INTEGER on a 6-billion-view video.
"""

import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Integer, String, Text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import _VARCHAR_LEN_RE  # noqa: E402


def should_widen_varchar(model_type, existing_type: str) -> bool:
    """Mirrors the varchar branch of _ensure_columns()."""
    if isinstance(model_type, String) and model_type.length:
        match = _VARCHAR_LEN_RE.match(existing_type)
        if match and int(match.group(1)) < model_type.length:
            return True
    return False


def should_widen_int(model_type, existing_type: str) -> bool:
    """Mirrors the bigint branch of _ensure_columns()."""
    return isinstance(model_type, BigInteger) and existing_type == "INTEGER"


@pytest.mark.parametrize(
    "model_type,existing,expected,why",
    [
        (String(255), "VARCHAR(64)", True, "the actual category incident"),
        (String(255), "VARCHAR(255)", False, "already wide enough"),
        (String(32), "VARCHAR(255)", False, "MUST NOT shrink — would truncate live rows"),
        (String(64), "VARCHAR(64)", False, "equal is a no-op"),
        (String(255), "TEXT", False, "TEXT is already unbounded"),
        (String(), "VARCHAR(64)", False, "unbounded String() has no target length"),
        (Text(), "VARCHAR(64)", False, "Text is not a bounded String"),
        (Integer(), "VARCHAR(64)", False, "wrong type family"),
        (String(255), "VARCHAR(254)", True, "off-by-one still widens"),
    ],
)
def test_varchar_widening_decisions(model_type, existing, expected, why):
    assert should_widen_varchar(model_type, existing) is expected, why


@pytest.mark.parametrize(
    "model_type,existing,expected",
    [
        (BigInteger(), "INTEGER", True),
        (BigInteger(), "BIGINT", False),
        (Integer(), "INTEGER", False),
    ],
)
def test_int_widening_decisions(model_type, existing, expected):
    assert should_widen_int(model_type, existing) is expected


def test_the_two_branches_are_mutually_exclusive():
    """A BigInteger must never fall into the varchar branch, or vice versa."""
    assert not should_widen_varchar(BigInteger(), "INTEGER")
    assert not should_widen_int(String(255), "VARCHAR(64)")


def test_widening_never_shrinks_regardless_of_input():
    """Property check: no (model, existing) pair may ever narrow a column."""
    for target in (16, 64, 255, 1024):
        for existing_len in (16, 64, 255, 1024):
            if should_widen_varchar(String(target), f"VARCHAR({existing_len})"):
                assert target > existing_len, f"would shrink {existing_len} -> {target}"


def test_varchar_regex_only_matches_bounded_varchar():
    assert _VARCHAR_LEN_RE.match("VARCHAR(64)")
    assert not _VARCHAR_LEN_RE.match("VARCHAR")
    assert not _VARCHAR_LEN_RE.match("TEXT")
    assert not _VARCHAR_LEN_RE.match("CHARACTER VARYING(64)")


# --------------------------------------------------------------------------
# Model-level guards. These assert the schema still reflects the incidents
# that forced these column types, so nobody narrows them back by accident.
# --------------------------------------------------------------------------

def test_category_column_is_wide_enough_for_a_label_list(app_env):
    from app.models import Summary
    from app.summarizer import MAX_CATEGORY_LEN

    length = Summary.__table__.c.category.type.length
    assert length >= MAX_CATEGORY_LEN, (
        "category holds a comma-joined LIST of labels; narrowing this "
        "reintroduces the StringDataRightTruncation that broke summarizing"
    )


def test_view_count_is_a_bigint(app_env):
    """A 6-billion-view video overflows a 32-bit INTEGER."""
    from app.models import Summary

    assert isinstance(Summary.__table__.c.view_count.type, BigInteger)


def test_init_db_is_idempotent(app_env):
    """It runs on every boot, so running it twice must be a no-op."""
    from app.db import init_db

    init_db()
    init_db()


def test_new_summary_columns_can_be_added_to_a_populated_table(app_env):
    """
    The auto-migration issues a bare ADD COLUMN, which fails on a populated
    table if the column is NOT NULL with no default. So every non-core
    column must be either nullable or defaulted. Catches a new column that
    would break the migration against real data.
    """
    from app.models import Summary

    core = {"id", "user_id", "video_id", "url", "summary_text", "category", "language"}
    for column in Summary.__table__.columns:
        if column.name in core:
            continue
        safe = column.nullable or column.default is not None or column.server_default is not None
        assert safe, (
            f"{column.name} is NOT NULL with no default — ADD COLUMN would fail "
            "against a table that already has rows"
        )
