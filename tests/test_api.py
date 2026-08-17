"""
API-level tests: auth, ownership boundaries, history, feedback, tags,
export.

The ownership tests are the most important thing in this file. Every
endpoint that takes an id must scope it to the calling user — a miss there
leaks one person's library to another, and it's the kind of bug that is
invisible in single-user manual testing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_signup_then_me(signed_in):
    client, email = signed_in
    assert client.get("/auth/me").json()["email"] == email


def test_duplicate_signup_is_rejected(client):
    payload = {"email": "dupe@example.com", "password": "CorrectHorse9!", "full_name": "D"}
    assert client.post("/auth/signup", json=payload).status_code == 200
    assert client.post("/auth/signup", json=payload).status_code >= 400


def test_login_with_wrong_password_fails(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "CorrectHorse9!", "full_name": "A"})
    res = client.post("/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert res.status_code >= 400


def test_logout_clears_the_session(signed_in):
    client, _ = signed_in
    client.post("/auth/logout")
    body = client.get("/auth/me").json()
    assert body is None or body.get("email") is None


@pytest.mark.parametrize("path", ["/history", "/glossary", "/tags", "/summarize/pending"])
def test_protected_endpoints_reject_anonymous_callers(client, path):
    assert client.get(path).status_code in (401, 403)


# --------------------------------------------------------------------------
# ownership — one user must never reach another's rows
# --------------------------------------------------------------------------

@pytest.fixture
def two_users(client, make_summary):
    client.post("/auth/signup", json={"email": "one@example.com", "password": "CorrectHorse9!", "full_name": "One"})
    first_id = make_summary(email="one@example.com", video_id="ownedbyone")
    client.post("/auth/logout")
    client.post("/auth/signup", json={"email": "two@example.com", "password": "CorrectHorse9!", "full_name": "Two"})
    return client, first_id


def test_history_only_returns_your_own_rows(two_users):
    client, _ = two_users
    assert client.get("/history").json() == []


def test_cannot_read_another_users_summary_via_status_patch(two_users):
    client, other_id = two_users
    assert client.patch(f"/history/{other_id}", json={"status": "read"}).status_code == 404


def test_cannot_rate_another_users_summary(two_users):
    client, other_id = two_users
    res = client.patch(f"/history/{other_id}/feedback", json={"answers": {"worth_it": "definitely"}})
    assert res.status_code == 404


def test_cannot_delete_another_users_summary(two_users):
    client, other_id = two_users
    assert client.delete(f"/history/{other_id}").status_code == 404


def test_tags_are_scoped_per_user(client, make_summary):
    client.post("/auth/signup", json={"email": "one@example.com", "password": "CorrectHorse9!", "full_name": "One"})
    make_summary(email="one@example.com", tags=["Proxmox"])
    assert [t["tag"] for t in client.get("/tags").json()] == ["Proxmox"]
    client.post("/auth/logout")
    client.post("/auth/signup", json={"email": "two@example.com", "password": "CorrectHorse9!", "full_name": "Two"})
    assert client.get("/tags").json() == []


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def test_saved_summary_appears_in_history(signed_in, make_summary):
    client, _ = signed_in
    make_summary(title="Findable Title")
    items = client.get("/history").json()
    assert len(items) == 1 and items[0]["title"] == "Findable Title"


def test_status_transitions(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    assert client.patch(f"/history/{sid}", json={"status": "read"}).json()["status"] == "read"
    assert [i["id"] for i in client.get("/history?status=read").json()] == [sid]
    assert client.get("/history?status=unread").json() == []


def test_invalid_status_is_rejected(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    assert client.patch(f"/history/{sid}", json={"status": "banana"}).status_code == 422


def test_search_matches_title(signed_in, make_summary):
    client, _ = signed_in
    make_summary(video_id="v1", title="Proxmox homelab tour")
    make_summary(video_id="v2", title="Something unrelated")
    found = client.get("/history?q=proxmox").json()
    assert [i["video_id"] for i in found] == ["v1"]


def test_category_filter(signed_in, make_summary):
    client, _ = signed_in
    make_summary(video_id="v1", category="Runescape, Gaming")
    make_summary(video_id="v2", category="Science")
    found = client.get("/history?category=Runescape").json()
    assert [i["video_id"] for i in found] == ["v1"]


def test_delete_removes_the_row(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    assert client.delete(f"/history/{sid}").status_code == 200
    assert client.get("/history").json() == []


def test_json_columns_round_trip(signed_in, make_summary):
    """Regression guard: these are stored as JSON text and hand-parsed back."""
    client, _ = signed_in
    make_summary(
        tags=["Proxmox", "Docker"],
        key_points=[{"point": "P", "detail": "D"}],
        glossary=[{"term": "T", "definition": "Def", "example": "Ex"}],
        chapters=[{"label": "Intro", "seconds": 0}],
        comment_tally={"positive": {"count": 3, "likes": 9}},
    )
    item = client.get("/history").json()[0]
    assert item["tags"] == ["Proxmox", "Docker"]
    assert item["key_points"] == [{"point": "P", "detail": "D"}]
    assert item["glossary"][0]["term"] == "T"
    assert item["chapters"] == [{"label": "Intro", "seconds": 0}]
    assert item["comment_tally"]["positive"]["count"] == 3


# --------------------------------------------------------------------------
# questions / feedback
# --------------------------------------------------------------------------

def test_question_set_is_served(client):
    questions = client.get("/questions").json()
    assert len(questions) >= 5
    for q in questions:
        assert q["key"] and q["prompt"] and len(q["options"]) >= 2


def test_watch_plan_question_exists(client):
    """The product's core signal: was the summary enough, or watch it anyway."""
    keys = [q["key"] for q in client.get("/questions").json()]
    assert "watch_plan" in keys


def test_answers_accumulate_without_clobbering(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    client.patch(f"/history/{sid}/feedback", json={"answers": {"worth_it": "definitely"}})
    res = client.patch(f"/history/{sid}/feedback", json={"answers": {"depth": "just_right"}})
    assert res.json()["feedback"] == {"worth_it": "definitely", "depth": "just_right"}


def test_empty_value_clears_one_answer(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    client.patch(f"/history/{sid}/feedback", json={"answers": {"worth_it": "definitely"}})
    res = client.patch(f"/history/{sid}/feedback", json={"answers": {"worth_it": ""}})
    assert "worth_it" not in (res.json()["feedback"] or {})


def test_unknown_question_rejected(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    res = client.patch(f"/history/{sid}/feedback", json={"answers": {"not_a_question": "x"}})
    assert res.status_code == 422


def test_invalid_answer_value_rejected(signed_in, make_summary):
    """Guards the dataset against a stale client sending stale option values."""
    client, _ = signed_in
    sid = make_summary()
    res = client.patch(f"/history/{sid}/feedback", json={"answers": {"worth_it": "sorta"}})
    assert res.status_code == 422


def test_every_declared_option_is_actually_accepted(signed_in, make_summary):
    """The served options and the validator must not drift apart."""
    client, _ = signed_in
    sid = make_summary()
    for q in client.get("/questions").json():
        for option in q["options"]:
            res = client.patch(f"/history/{sid}/feedback", json={"answers": {q["key"]: option["value"]}})
            assert res.status_code == 200, f'{q["key"]}={option["value"]} rejected'


def test_feedback_persists(signed_in, make_summary):
    client, _ = signed_in
    sid = make_summary()
    client.patch(f"/history/{sid}/feedback", json={"answers": {"watch_plan": "summary_enough"}})
    assert client.get("/history").json()[0]["feedback"] == {"watch_plan": "summary_enough"}


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

def test_tags_aggregate_with_counts_most_used_first(signed_in, make_summary):
    client, _ = signed_in
    make_summary(video_id="v1", tags=["Proxmox", "Docker"])
    make_summary(video_id="v2", tags=["Proxmox"])
    tags = client.get("/tags").json()
    assert tags[0] == {"tag": "Proxmox", "count": 2}
    assert {t["tag"] for t in tags} == {"Proxmox", "Docker"}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["json", "csv", "markdown"])
def test_export_formats_return_content(signed_in, make_summary, fmt):
    client, _ = signed_in
    make_summary(title="Exported Video")
    res = client.get(f"/history/export?format={fmt}")
    assert res.status_code == 200
    assert "Exported Video" in res.text


def test_export_rejects_unknown_format(signed_in):
    client, _ = signed_in
    assert client.get("/history/export?format=xml").status_code == 422


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------

def test_pending_starts_empty(signed_in):
    client, _ = signed_in
    assert client.get("/summarize/pending").json() == []


def test_dismissing_a_nonexistent_pending_row_404s(signed_in):
    client, _ = signed_in
    assert client.delete("/summarize/pending/99999").status_code == 404


def test_retrying_a_nonexistent_pending_row_404s(signed_in):
    client, _ = signed_in
    assert client.post("/summarize/pending/99999/retry").status_code == 404


# --------------------------------------------------------------------------
# CORS — this exists to stop a third-party page making authenticated calls
# --------------------------------------------------------------------------

def test_allowed_origin_is_echoed(client):
    res = client.get("/health", headers={"Origin": "https://www.toolazydidntwatch.com"})
    assert res.headers.get("access-control-allow-origin") == "https://www.toolazydidntwatch.com"


def test_foreign_origin_gets_no_cors_header(client):
    res = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in res.headers


def test_lookalike_domain_is_not_allowed(client):
    res = client.get("/health", headers={"Origin": "https://toolazydidntwatch.com.attacker.net"})
    assert "access-control-allow-origin" not in res.headers


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_every_method_the_api_uses_is_allowed_in_preflight(client, method):
    res = client.options(
        "/history",
        headers={
            "Origin": "https://www.toolazydidntwatch.com",
            "Access-Control-Request-Method": method,
        },
    )
    assert method in res.headers.get("access-control-allow-methods", "")
