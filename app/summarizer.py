"""
Summarization via Claude, GPT, or Gemini (user's account-level choice).

Supports per-user length/format/provider preferences, a direct answer
to whatever the video's title promises, chapter timestamps pulled from
the description/comments, and comment-based sentiment scoring — all
folded into one LLM call so extra features never cost a second
round-trip.
"""

import os
import re
from anthropic import Anthropic

ANTHROPIC_MODEL = "claude-sonnet-4-6"
OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-2.0-flash"
MAX_TOKENS = 2048


class SummarizerError(Exception):
    pass


# ---------- provider dispatch ----------

def _call_anthropic(prompt: str, client: Anthropic | None = None) -> str:
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SummarizerError("Server is missing ANTHROPIC_API_KEY.")
        client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(blocks).strip()
    except Exception as e:
        raise SummarizerError(f"Anthropic call failed ({e.__class__.__name__}): {e}")


def _call_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SummarizerError("Server is missing OPENAI_API_KEY.")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()
    except SummarizerError:
        raise
    except Exception as e:
        raise SummarizerError(f"OpenAI call failed ({e.__class__.__name__}): {e}")


def _call_gemini(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SummarizerError("Server is missing GEMINI_API_KEY.")
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return (response.text or "").strip()
    except SummarizerError:
        raise
    except Exception as e:
        raise SummarizerError(f"Gemini call failed ({e.__class__.__name__}): {e}")


def _call_provider(provider: str, prompt: str, client: Anthropic | None = None) -> str:
    if provider == "openai":
        return _call_openai(prompt)
    if provider == "gemini":
        return _call_gemini(prompt)
    return _call_anthropic(prompt, client=client)


CATEGORIES = [
    "Tech", "AI", "Agents", "Courses", "Business", "Finance", "Entertainment",
    "Comedy", "Music", "Gaming", "News", "Politics", "Science", "Education",
    "Health", "Fitness", "Cooking", "Travel", "Sports", "DIY & How-To",
    "Reviews", "Documentary", "Other",
]

_BULLET_COUNTS = {"brief": (1, 3), "standard": (4, 8), "detailed": (6, 10)}

_TIMESTAMP_HINT_RE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")


def _looks_like_it_has_timestamps(description: str | None, comments: list[dict] | None) -> bool:
    """
    Cheap pre-check so we only ever ask the model for chapter timestamps
    when there's a real *list* of them to find — otherwise a model eager
    to fill out the requested format will invent plausible-looking
    chapters that were never in the source. Requires 2+ MM:SS-shaped
    matches in the SAME field (a single incidental mention, e.g. someone
    noting "this is only 0:18 long", isn't a chapter list).
    """
    if description and len(_TIMESTAMP_HINT_RE.findall(description)) >= 2:
        return True
    if comments:
        for c in comments:
            if len(_TIMESTAMP_HINT_RE.findall(c.get("text", ""))) >= 2:
                return True
    return False


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
    title: str | None = None,
    description: str | None = None,
) -> str:
    category_list = ", ".join(CATEGORIES)
    structure = _structure_instructions(length, format)

    header_lines = [f"CATEGORY: <pick the single best-fitting label from: {category_list}>"]

    if title:
        header_lines.append(
            f'ANSWER: <if the title "{title}" poses a question or promises to reveal/explain '
            "something, 1-2 sentences directly resolving it based on the transcript. "
            "If the title isn't a question or claim, write NONE>"
        )

    if _looks_like_it_has_timestamps(description, comments):
        header_lines.append(
            "TIMESTAMPS: <the description or comments below appear to contain chapter "
            "timestamps (MM:SS style). Extract ONLY the ones explicitly written there, output "
            "each on its own line right after this one, formatted exactly 'MM:SS | Label' (or "
            "H:MM:SS for longer videos) — one chapter per line, no blank lines between them, "
            "then immediately the '---' line. Do not add, guess, or infer any timestamp that "
            "isn't literally written in the description/comments below.>"
        )

    if comments:
        header_lines.append(
            "SENTIMENT: <one of Positive, Mostly Positive, Mixed, Mostly Negative, Negative> "
            "— <1-2 sentence blurb on common themes in the comments>"
        )

    header_lines.append("---")
    header = "\n".join(header_lines)

    extra_context = ""
    if description:
        extra_context += (
            "\n\nVIDEO DESCRIPTION (use only to find chapter timestamps if present, "
            f"not for the summary itself):\n{description[:3000]}"
        )
    if comments:
        comment_lines = "\n".join(f'[{c["like_count"]} likes] "{c["text"]}"' for c in comments)
        extra_context += (
            "\n\nTOP COMMENTS (shown with like counts — weight your sentiment assessment "
            f"toward the higher-liked ones; also check these for timestamp callouts):\n{comment_lines}"
        )

    title_line = f'the video titled "{title}"' if title else "the following YouTube video"

    return (
        f"Summarize {title_line} for someone deciding whether to watch it and/or trying to "
        "get the key points quickly.\n\n"
        "Respond in exactly this format:\n"
        f"{header}\n\n"
        f"{structure}\n"
        "Be concise. Don't pad. Don't restate the instructions.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
        f"{extra_context}"
    )


def _parse_timestamp(ts: str) -> int | None:
    parts = ts.strip().split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        return None
    return h * 3600 + m * 60 + s


def _parse_response(raw_text: str) -> dict:
    """
    Pull the optional CATEGORY:/ANSWER:/SENTIMENT:/TIMESTAMPS: header
    lines off the model's response, up to a line containing only '---'.
    Falls back to today's category-only parsing if the model doesn't
    emit the '---' delimiter (e.g. an older/less steerable model).
    """
    lines = raw_text.split("\n")
    category = "Other"
    answer = None
    sentiment_label = None
    sentiment_blurb = None
    chapters = []
    idx = 0

    while idx < len(lines):
        # Models sometimes markdown-bold the header labels ("**SENTIMENT:**
        # ...") despite being asked for plain "LABEL: value" — normalize
        # that away before matching, without touching the original `lines`
        # (the summary body is sliced from `lines` untouched below).
        stripped = lines[idx].strip().replace("**", "")
        stripped = stripped.lstrip("-*# ").strip()
        upper = stripped.upper()

        if upper == "---":
            idx += 1
            break
        elif not stripped:
            idx += 1  # blank line between header sections — keep scanning for '---'
        elif upper.startswith("CATEGORY:"):
            category = stripped.split(":", 1)[1].strip() or "Other"
            idx += 1
        elif upper.startswith("ANSWER:"):
            val = stripped.split(":", 1)[1].strip()
            answer = None if not val or val.upper() == "NONE" else val
            idx += 1
        elif upper.startswith("SENTIMENT:"):
            sentiment_raw = stripped.split(":", 1)[1].strip()
            if "—" in sentiment_raw:
                label, blurb = sentiment_raw.split("—", 1)
            elif " - " in sentiment_raw:
                label, blurb = sentiment_raw.split(" - ", 1)
            else:
                label, blurb = sentiment_raw, ""
            sentiment_label = label.strip() or None
            sentiment_blurb = blurb.strip() or None
            idx += 1
        elif upper.startswith("TIMESTAMPS:"):
            idx += 1
            while idx < len(lines):
                sub = lines[idx].strip().replace("**", "").lstrip("-*# ").strip()
                if not sub or sub.upper() == "---" or "|" not in sub:
                    break
                ts_part, label_part = sub.split("|", 1)
                seconds = _parse_timestamp(ts_part)
                if seconds is not None and label_part.strip():
                    chapters.append({"label": label_part.strip(), "seconds": seconds})
                idx += 1
        else:
            break  # unrecognized line -> start of summary body

    summary = "\n".join(lines[idx:]).strip()
    return {
        "category": category,
        "summary": summary,
        "answer": answer,
        "sentiment_label": sentiment_label,
        "sentiment_blurb": sentiment_blurb,
        "chapters": chapters,
    }


def summarize(
    transcript_text: str,
    client: Anthropic | None = None,
    length: str = "standard",
    format: str = "mixed",
    comments: list[dict] | None = None,
    title: str | None = None,
    description: str | None = None,
    provider: str = "anthropic",
) -> dict:
    """
    Summarize a transcript with the chosen provider. Returns
    {"summary", "category", "answer", "sentiment_label", "sentiment_blurb", "chapters"}.
    The optional fields are None/[] unless their inputs were provided.
    Raises SummarizerError on failure.
    """
    if not transcript_text.strip():
        raise SummarizerError("Empty transcript — nothing to summarize.")

    truncated = transcript_text[:100_000]
    prompt = build_prompt(
        truncated, length=length, format=format, comments=comments,
        title=title, description=description,
    )

    raw = _call_provider(provider, prompt, client=client)
    if not raw:
        raise SummarizerError("The model returned an empty response.")

    parsed = _parse_response(raw)
    if not parsed["summary"]:
        raise SummarizerError("The model returned an empty response.")
    return parsed
