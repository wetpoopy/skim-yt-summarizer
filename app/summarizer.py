"""
Summarization via the Claude API.

Supports per-user length/format preferences and optional comment-based
sentiment scoring, folded into the same call as the summary itself so
it never costs a second LLM round-trip.
"""

import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_TRANSCRIPT_CHARS = 100_000  # rough safety cap before we bother chunking
MAX_TOKENS = 1536


class SummarizerError(Exception):
    pass


def _get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SummarizerError("Server is missing ANTHROPIC_API_KEY.")
    return Anthropic(api_key=api_key)


CATEGORIES = [
    "Tech", "AI", "Agents", "Courses", "Business", "Finance", "Entertainment",
    "Comedy", "Music", "Gaming", "News", "Politics", "Science", "Education",
    "Health", "Fitness", "Cooking", "Travel", "Sports", "DIY & How-To",
    "Reviews", "Documentary", "Other",
]

_BULLET_COUNTS = {"brief": (1, 3), "standard": (4, 8), "detailed": (6, 10)}


def _structure_instructions(length: str, fmt: str) -> str:
    lo, hi = _BULLET_COUNTS.get(length, _BULLET_COUNTS["standard"])

    if fmt == "bullets":
        takeaway = "" if length == "brief" else "- One bullet for anything notably actionable, surprising, or a key takeaway (skip if nothing stands out)\n"
        return (
            "Respond entirely in bullet points — no prose paragraphs.\n"
            "- One bullet TL;DR\n"
            f"- {lo}-{hi} bullets covering the main content\n"
            f"{takeaway}"
        )

    if fmt == "prose":
        depth = {"brief": "1 short paragraph", "standard": "2-3 paragraphs", "detailed": "4-5 paragraphs"}[length]
        return (
            "Respond in flowing prose — no bullet points, no headers.\n"
            f"Write {depth}: open with what the video is about and the key takeaway, "
            "then cover the main content, in clear sentences."
        )

    # mixed (default) — TL;DR + bullets + takeaway
    tl_dr = "A 2-3 sentence TL;DR" if length == "detailed" else "One-sentence TL;DR"
    takeaway = "" if length == "brief" else "3. Anything notably actionable, surprising, or a key takeaway\n"
    return (
        f"1. {tl_dr}\n"
        f"2. {lo}-{hi} bullet points covering the main content\n"
        f"{takeaway}"
    )


def build_prompt(
    transcript_text: str,
    length: str = "standard",
    format: str = "mixed",
    comments: list[dict] | None = None,
) -> str:
    category_list = ", ".join(CATEGORIES)
    structure = _structure_instructions(length, format)

    header_lines = [f"CATEGORY: <pick the single best-fitting label from: {category_list}>"]
    comments_block = ""
    if comments:
        comment_lines = "\n".join(f'[{c["like_count"]} likes] "{c["text"]}"' for c in comments)
        header_lines.append(
            "SENTIMENT: <one of Positive, Mostly Positive, Mixed, Mostly Negative, Negative> "
            "— <1-2 sentence blurb on common themes in the comments>"
        )
        comments_block = (
            "\n\nTOP COMMENTS (shown with like counts — weight your sentiment assessment "
            f"toward the higher-liked ones):\n{comment_lines}"
        )

    header = "\n".join(header_lines)

    return (
        "Summarize the following YouTube video transcript for someone deciding "
        "whether to watch it and/or trying to get the key points quickly.\n\n"
        "Respond in exactly this format:\n"
        f"{header}\n\n"
        f"{structure}\n"
        "Be concise. Don't pad. Don't restate the instructions.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
        f"{comments_block}"
    )


def _parse_response(raw_text: str) -> dict:
    """Pull CATEGORY: and (optional) SENTIMENT: header lines off the model's response."""
    lines = raw_text.split("\n")
    category = "Other"
    sentiment_label = None
    sentiment_blurb = None
    idx = 0

    if idx < len(lines) and lines[idx].strip().upper().startswith("CATEGORY:"):
        category = lines[idx].split(":", 1)[1].strip() or "Other"
        idx += 1

    if idx < len(lines) and lines[idx].strip().upper().startswith("SENTIMENT:"):
        sentiment_raw = lines[idx].split(":", 1)[1].strip()
        idx += 1
        if "—" in sentiment_raw:
            label, blurb = sentiment_raw.split("—", 1)
        elif " - " in sentiment_raw:
            label, blurb = sentiment_raw.split(" - ", 1)
        else:
            label, blurb = sentiment_raw, ""
        sentiment_label = label.strip() or None
        sentiment_blurb = blurb.strip() or None

    summary = "\n".join(lines[idx:]).strip()
    return {
        "category": category,
        "summary": summary,
        "sentiment_label": sentiment_label,
        "sentiment_blurb": sentiment_blurb,
    }


def summarize(
    transcript_text: str,
    client: Anthropic | None = None,
    length: str = "standard",
    format: str = "mixed",
    comments: list[dict] | None = None,
) -> dict:
    """
    Summarize a transcript with Claude. Returns
    {"summary", "category", "sentiment_label", "sentiment_blurb"} — the
    sentiment fields are None unless `comments` was provided.
    Raises SummarizerError on failure.
    """
    if not transcript_text.strip():
        raise SummarizerError("Empty transcript — nothing to summarize.")

    truncated = transcript_text[:MAX_TRANSCRIPT_CHARS]
    client = client or _get_client()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": build_prompt(truncated, length=length, format=format, comments=comments),
            }],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        raw = "\n".join(text_blocks).strip()
        if not raw:
            raise SummarizerError("Claude returned an empty response.")
        parsed = _parse_response(raw)
        if not parsed["summary"]:
            raise SummarizerError("Claude returned an empty response.")
        return parsed
    except SummarizerError:
        raise
    except Exception as e:
        raise SummarizerError(f"Summarization failed ({e.__class__.__name__}): {e}")
