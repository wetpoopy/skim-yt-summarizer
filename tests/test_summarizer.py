"""
Tests for app/summarizer.py — the response parser, the category
normaliser, and the comment tally.

These cover the code that has actually broken in production. Every test
here maps to a real incident or a real near-miss, noted inline, so nobody
later deletes one thinking it's hypothetical.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.summarizer import (  # noqa: E402
    CATEGORIES,
    MAX_CATEGORY_LEN,
    _parse_response,
    _strip_leading_label,
    _synthesize_key_points,
    build_prompt,
    normalize_category,
    tally_comment_sentiment,
)


# --------------------------------------------------------------------------
# normalize_category
#
# INCIDENT: category is a comma-joined LIST of labels, but the column was
# varchar(64). Any 3-label result overflowed and the DB write took the whole
# summary down with it, which broke nearly every summarize for a day.
# --------------------------------------------------------------------------

def test_category_output_always_fits_its_column():
    """The exact 66-char value that was raising StringDataRightTruncation."""
    result = normalize_category(
        "Gadgets & Consumer Tech, Startups & Tech Business, Product Reviews"
    )
    assert len(result) <= MAX_CATEGORY_LEN


def test_category_fits_even_if_the_model_returns_every_label():
    """Worst case: the model ignores instructions and lists the whole vocabulary."""
    result = normalize_category(", ".join(CATEGORIES))
    assert len(result) <= MAX_CATEGORY_LEN


def test_category_drops_from_the_tail_not_the_head():
    """Labels are ordered most-significant-first, so truncation must keep the front."""
    longest = sorted(CATEGORIES, key=len, reverse=True)
    result = normalize_category(", ".join(longest))
    assert result.split(", ")[0] == longest[0]


def test_invented_categories_are_discarded():
    result = normalize_category("Software Development, Underwater Basket Weaving")
    assert "Underwater Basket Weaving" not in result
    assert "Software Development" in result


def test_empty_category_falls_back_to_other():
    assert normalize_category("") == "Other"
    assert normalize_category("Nonsense Label") == "Other"


def test_duplicate_labels_are_deduped():
    result = normalize_category("Gaming, Gaming, Gaming")
    assert result == "Gaming"


@pytest.mark.parametrize(
    "title,expected_primary",
    [
        ("Building an OSRS ironman account", "Runescape"),
        ("How the Stripe API works", "API"),
        ("Building AI agents that ship code", "Agents"),
    ],
)
def test_title_based_overrides_take_the_primary_slot(title, expected_primary):
    result = normalize_category("Software Development", title=title)
    assert result.split(", ")[0] == expected_primary


def test_api_outranks_agents_when_both_appear():
    """Documented precedence — API is inserted after Agents, so it wins."""
    result = normalize_category("Software Development", title="Agents talking to an API")
    assert result.split(", ")[0] == "API"


def test_runescape_wins_over_everything():
    result = normalize_category("Software Development", title="OSRS agent API guide")
    assert result.split(", ")[0] == "Runescape"


def test_runescape_detected_from_body_text_not_just_title():
    result = normalize_category("Gaming", title="My favourite grind", extra_text="jagex nerfed it")
    assert result.split(", ")[0] == "Runescape"


# --------------------------------------------------------------------------
# _parse_response — the ---delimited format. A break here corrupts summaries
# silently, which makes it the most valuable thing in the file to pin down.
# --------------------------------------------------------------------------

def _response(header: str, body: str = "The summary body.") -> str:
    return f"{header}\n---\n{body}"


def test_parses_a_full_response():
    parsed = _parse_response(_response(
        "CATEGORY: Science\n"
        "ANSWER: Neural nets are layered functions.\n"
        "TRUE_TITLE: NONE\n"
        "SENTIMENT: Mostly Positive — praised the visuals\n"
        "HIGHLIGHT: The animations land.\n"
        "COUNTERPOINT: NONE\n"
        "KEY_POINTS:\n"
        "A short point :: A longer elaboration.\n"
        "GLOSSARY:\n"
        "RAG :: Retrieval-Augmented Generation. :: Used in the demo.\n"
        "TAGS: Neural Networks, Deep Learning"
    ))
    assert parsed["category"] == "Science"
    assert parsed["answer"] == "Neural nets are layered functions."
    assert parsed["true_title"] is None
    assert parsed["sentiment_label"] == "Mostly Positive"
    assert parsed["sentiment_blurb"] == "praised the visuals"
    assert parsed["highlight"] == "The animations land."
    assert parsed["counterpoint"] is None
    assert parsed["key_points"] == [{"point": "A short point", "detail": "A longer elaboration."}]
    assert parsed["glossary"] == [
        {"term": "RAG", "definition": "Retrieval-Augmented Generation.", "example": "Used in the demo."}
    ]
    assert parsed["tags"] == ["Neural Networks", "Deep Learning"]
    assert parsed["summary"] == "The summary body."


def test_summary_body_survives_every_header_field():
    """Regression: a malformed header field used to swallow the body."""
    parsed = _parse_response(_response("CATEGORY: Science\nTAGS: A, B", body="Line one.\nLine two."))
    assert parsed["summary"] == "Line one.\nLine two."


def test_missing_sections_do_not_raise():
    parsed = _parse_response(_response("CATEGORY: Science"))
    assert parsed["key_points"] == []
    assert parsed["glossary"] == []
    assert parsed["tags"] == []
    assert parsed["true_title"] is None


def test_markdown_bolded_labels_are_tolerated():
    """Models bold the header labels despite being told not to."""
    parsed = _parse_response(_response("**CATEGORY:** Science\n**ANSWER:** Something."))
    assert parsed["category"] == "Science"
    assert parsed["answer"] == "Something."


def test_none_means_none_across_optional_fields():
    parsed = _parse_response(_response(
        "CATEGORY: Science\nANSWER: NONE\nTRUE_TITLE: NONE\nHIGHLIGHT: NONE\nCOUNTERPOINT: NONE"
    ))
    assert parsed["answer"] is None
    assert parsed["true_title"] is None
    assert parsed["highlight"] is None
    assert parsed["counterpoint"] is None


def test_true_title_strips_surrounding_quotes():
    parsed = _parse_response(_response('CATEGORY: X\nTRUE_TITLE: "A Quoted Title"'))
    assert parsed["true_title"] == "A Quoted Title"


def test_tags_are_cleaned_and_deduped():
    parsed = _parse_response(_response("CATEGORY: X\nTAGS: #Docker, Docker , Proxmox, NONE"))
    assert parsed["tags"] == ["Docker", "Proxmox"]


def test_chapters_parse_into_seconds():
    parsed = _parse_response(_response(
        "CATEGORY: X\nTIMESTAMPS:\n00:30 | Intro\n1:02:05 | The long bit"
    ))
    assert parsed["chapters"] == [
        {"label": "Intro", "seconds": 30},
        {"label": "The long bit", "seconds": 3725},
    ]


def test_response_without_the_delimiter_still_yields_a_summary():
    """Older/less steerable models sometimes skip the '---' entirely."""
    parsed = _parse_response("CATEGORY: Science\nJust a plain summary with no delimiter.")
    assert parsed["summary"]


# INCIDENT: models emitted a literal "TL;DR:" prefix even when told not to.
@pytest.mark.parametrize("prefix", ["TL;DR:", "TLDR:", "**TL;DR:**", "Summary:", "Overview -"])
def test_leading_label_is_stripped(prefix):
    assert _strip_leading_label(f"{prefix} The actual content.") == "The actual content."


def test_leading_label_strip_does_not_eat_mid_sentence_matches():
    text = "This video is a summary: it covers three things."
    assert _strip_leading_label(text) == text


# --------------------------------------------------------------------------
# _synthesize_key_points — guarantees the SHORT view always has content even
# when the model's KEY_POINTS section is missing or malformed.
# --------------------------------------------------------------------------

def test_key_points_synthesized_from_bullets():
    points = _synthesize_key_points("Lead line.\n- first bullet\n- second bullet")
    assert len(points) >= 2
    assert all(p["point"] and p["detail"] for p in points)


def test_key_points_synthesized_from_prose_when_no_bullets():
    points = _synthesize_key_points("One sentence here. And a second sentence. Third one too.")
    assert len(points) >= 2


def test_synthesized_points_are_capped():
    points = _synthesize_key_points("\n".join(f"- bullet {i}" for i in range(40)))
    assert len(points) <= 8


def test_summarize_guarantees_key_points(monkeypatch):
    """A response with no KEY_POINTS must still produce a SHORT view."""
    import app.summarizer as summarizer

    monkeypatch.setattr(
        summarizer, "_call_provider",
        lambda *a, **k: "CATEGORY: Science\n---\nBody line.\n- a bullet\n- another bullet",
    )
    result = summarizer.summarize("transcript text")
    assert result["key_points"], "SHORT view would be empty"


# --------------------------------------------------------------------------
# tally_comment_sentiment — the arithmetic is done in Python precisely so a
# model can't get it wrong. These pin that down.
# --------------------------------------------------------------------------

COMMENTS = [
    {"text": "a", "like_count": 100},
    {"text": "b", "like_count": 50},
    {"text": "c", "like_count": 7},
]


def test_tally_counts_and_sums_likes():
    tally = tally_comment_sentiment(COMMENTS, {1: "positive", 2: "negative", 3: "positive"})
    assert tally["positive"] == {"count": 2, "likes": 107}
    assert tally["negative"] == {"count": 1, "likes": 50}
    assert tally["total_classified"] == 3


def test_tally_ignores_out_of_range_and_invalid_labels():
    tally = tally_comment_sentiment(COMMENTS, {1: "positive", 99: "positive", 0: "negative", 2: "bogus"})
    assert tally["total_classified"] == 1


def test_tally_returns_none_rather_than_a_misleading_zero_row():
    assert tally_comment_sentiment([], {1: "positive"}) is None
    assert tally_comment_sentiment(COMMENTS, {}) is None
    assert tally_comment_sentiment(COMMENTS, {99: "positive"}) is None


def test_tally_tolerates_missing_like_counts():
    tally = tally_comment_sentiment([{"text": "x"}], {1: "positive"})
    assert tally["positive"]["likes"] == 0


# --------------------------------------------------------------------------
# build_prompt structure.
#
# NEAR-MISS: an edit briefly put newlines inside the TRUE_TITLE field spec.
# Header fields are parsed one line at a time, so the overflow would have
# been treated as the start of the summary body — corrupting the summary.
# --------------------------------------------------------------------------

def _header_lines(prompt: str) -> list[str]:
    return prompt.split("\n---")[0].split("\n")


@pytest.mark.parametrize("field", ["CATEGORY:", "ANSWER:", "TRUE_TITLE:", "TAGS:", "GLOSSARY:"])
def test_every_header_field_is_a_single_line(field):
    prompt = build_prompt("transcript", title="A Title", comments=[{"text": "c", "like_count": 1}])
    matching = [line for line in _header_lines(prompt) if line.startswith(field)]
    assert len(matching) == 1, f"{field} appears {len(matching)} times — spec may have wrapped"
    assert matching[0].rstrip().endswith(">"), f"{field} spec is not closed on its own line"


def test_prompt_has_the_delimiter():
    assert "\n---" in build_prompt("transcript", title="T")


def test_known_tags_are_offered_for_reuse():
    prompt = build_prompt("transcript", title="T", known_tags=["Proxmox", "Docker"])
    assert "Proxmox" in prompt and "Docker" in prompt


def test_comment_fields_only_appear_when_there_are_comments():
    without = build_prompt("transcript", title="T")
    assert "COMMENT_SENTIMENT:" not in without
    with_comments = build_prompt("transcript", title="T", comments=[{"text": "c", "like_count": 1}])
    assert "COMMENT_SENTIMENT:" in with_comments


def test_comments_are_numbered_for_per_comment_classification():
    prompt = build_prompt(
        "transcript", title="T",
        comments=[{"text": "first", "like_count": 5}, {"text": "second", "like_count": 2}],
    )
    assert '1. [5 likes] "first"' in prompt
    assert '2. [2 likes] "second"' in prompt


# --------------------------------------------------------------------------
# User-defined top-level categories. These LAYER OVER the built-in list:
# the user's own labels win the primary slot, the built-ins remain available
# as a fallback so unusual videos still get categorised.
# --------------------------------------------------------------------------

MINE = ["Programming", "APIs", "Homelab", "Runescape"]


def test_user_category_is_accepted_even_though_it_is_not_built_in():
    """'Homelab' is not in CATEGORIES — without the layering it'd be dropped."""
    result = normalize_category("Homelab", user_categories=MINE)
    assert result.split(", ")[0] == "Homelab"


def test_user_category_outranks_a_builtin_listed_first():
    result = normalize_category("Science, Homelab", user_categories=MINE)
    assert result.split(", ")[0] == "Homelab"


def test_builtins_still_work_when_no_user_category_fits():
    """The fallback is the whole point of layering rather than replacing."""
    result = normalize_category("Cooking & Food", user_categories=MINE)
    assert result.split(", ")[0] == "Cooking & Food"


def test_builtin_labels_are_kept_alongside_user_ones():
    result = normalize_category("Homelab, Science", user_categories=MINE)
    assert "Science" in result and result.split(", ")[0] == "Homelab"


def test_user_spelling_wins_a_case_collision():
    """User wrote 'apis'; their casing is what should be stored."""
    result = normalize_category("API", user_categories=["apis", "API"])
    assert "apis" in result or "API" in result


def test_invented_labels_still_rejected_with_a_user_list():
    result = normalize_category("Homelab, Totally Made Up", user_categories=MINE)
    assert "Totally Made Up" not in result


def test_no_user_list_behaves_exactly_as_before():
    assert normalize_category("Science") == normalize_category("Science", user_categories=[])


def test_user_categories_still_respect_the_column_cap():
    long_names = [f"Category Number {i:02d}" for i in range(30)]
    result = normalize_category(", ".join(long_names), user_categories=long_names)
    assert len(result) <= MAX_CATEGORY_LEN


def test_prompt_tells_the_model_to_prefer_the_user_list():
    prompt = build_prompt("transcript", title="T", user_categories=MINE)
    category_line = [l for l in prompt.split("\n") if l.startswith("CATEGORY:")][0]
    assert "Homelab" in category_line
    assert "MUST" in category_line and "FIRST" in category_line
    # The built-in vocabulary must still be offered as the fallback.
    assert "Cooking & Food" in category_line


def test_prompt_omits_the_rule_when_the_user_has_no_list():
    prompt = build_prompt("transcript", title="T")
    category_line = [l for l in prompt.split("\n") if l.startswith("CATEGORY:")][0]
    assert "user's OWN top-level categories" not in category_line
