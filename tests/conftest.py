"""
Shared test fixtures.

Two rules the whole suite follows:

1. **No network, no API keys.** Nothing here calls Anthropic, OpenAI,
   Gemini, or the YouTube API. Anything that would is either tested as a
   pure function or monkeypatched. This is what lets the suite run in CI
   on a fresh clone with no secrets.
2. **A fresh SQLite database per test.** The app already falls back to
   SQLite when DATABASE_URL is unset, so tests exercise the real schema
   and the real auto-migration path rather than a mock.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

# Import the app package from the repo root regardless of where pytest runs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """
    A fully isolated app instance backed by its own SQLite file.

    app.db builds its engine at import time from DATABASE_URL, so the env
    has to be set *before* the modules load — hence the reload dance. Each
    test gets its own file, so tests can't leak state into each other.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-long-enough-for-hs256-signing")
    # Present but never used — no test is allowed to make a real API call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import app.db
    import app.models
    import app.auth
    import app.main

    for module in (app.db, app.models, app.auth, app.main):
        importlib.reload(module)

    app.db.init_db()
    return app.main


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient

    return TestClient(app_env.app)


@pytest.fixture
def signed_in(client):
    """A registered, logged-in user. Returns the client plus their email."""
    email = "tester@example.com"
    res = client.post(
        "/auth/signup",
        json={"email": email, "password": "CorrectHorse9!", "full_name": "Tester"},
    )
    assert res.status_code == 200, res.text
    return client, email


@pytest.fixture
def make_summary(app_env):
    """
    Insert a Summary row directly.

    Deliberately bypasses /summarize: that endpoint needs a transcript, the
    YouTube API, and an LLM call. Everything downstream of a saved summary
    (history, filters, feedback, tags, export) can be tested honestly
    without any of that.
    """
    import json as _json

    from app.db import SessionLocal
    from app.models import Summary, User
    from sqlalchemy import select

    def _make(email="tester@example.com", **overrides):
        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.email == email))
            assert user is not None, f"no such user: {email}"
            fields = {
                "user_id": user.id,
                "video_id": "vid00000001",
                "url": "https://www.youtube.com/watch?v=vid00000001",
                "summary_text": "A summary body.\n- point one\n- point two",
                "category": "Software Development",
                "language": "en",
                "status": "unread",
                "title": "A Video Title",
            }
            for key in ("tags", "key_points", "glossary", "chapters", "comment_tally", "feedback"):
                if key in overrides:
                    value = overrides.pop(key)
                    column = {
                        "tags": "tags_json",
                        "key_points": "key_points_json",
                        "glossary": "glossary_json",
                        "chapters": "chapters_json",
                        "comment_tally": "comment_tally_json",
                        "feedback": "feedback_json",
                    }[key]
                    fields[column] = _json.dumps(value) if value is not None else None
            fields.update(overrides)
            row = Summary(**fields)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()

    return _make


@pytest.fixture
def frontend_source():
    """The frontend JavaScript, for JS-level assertions."""
    return (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
