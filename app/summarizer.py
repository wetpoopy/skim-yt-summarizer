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


class QuotaExceededError(SummarizerError):
    """Raised when a provider rejects the call for hitting a rate/quota
    limit — distinct from other failures so callers can stop a batch run
    instead of burning through the rest of it against the same wall."""
    pass


PROVIDER_LABELS = {"anthropic": "Claude", "openai": "GPT", "gemini": "Gemini"}
PROVIDER_BILLING_URLS = {
    "anthropic": "console.anthropic.com/settings/billing",
    "openai": "platform.openai.com/account/billing",
    "gemini": "ai.google.dev",
}


def _is_quota_error(e: Exception) -> bool:
    if getattr(e, "status_code", None) == 429 or getattr(e, "code", None) == 429:
        return True
    text = str(e).lower()
    return any(
        marker in text
        for marker in ("resource_exhausted", "rate_limit", "quota", "429", "insufficient_quota")
    )


def _quota_error(provider: str) -> QuotaExceededError:
    label = PROVIDER_LABELS.get(provider, provider)
    billing = PROVIDER_BILLING_URLS.get(provider, "")
    return QuotaExceededError(f"{label} is out of quota right now. Try a different model, or check billing at {billing}.")


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
        if _is_quota_error(e):
            raise _quota_error("anthropic")
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
        if _is_quota_error(e):
            raise _quota_error("openai")
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
        if _is_quota_error(e):
            raise _quota_error("gemini")
        raise SummarizerError(f"Gemini call failed ({e.__class__.__name__}): {e}")


def _call_provider(provider: str, prompt: str, client: Anthropic | None = None) -> str:
    if provider == "openai":
        return _call_openai(prompt)
    if provider == "gemini":
        return _call_gemini(prompt)
    return _call_anthropic(prompt, client=client)


CATEGORIES = [
    # Tech & software
    "Software Development", "AI & Machine Learning", "Agents", "Cybersecurity",
    "Gadgets & Consumer Tech", "Startups & Tech Business", "Web Development",
    "Data & Analytics",
    # Business & finance
    "Personal Finance", "Investing & Markets", "Cryptocurrency",
    "Entrepreneurship", "Economics", "Career & Productivity",
    # Gaming
    "Gaming", "Runescape", "Esports",
    # Education & learning
    "Courses & Tutorials", "Science", "History", "Language Learning",
    "Self-Improvement",
    # Entertainment & culture
    "Movies & TV", "Celebrity & Pop Culture", "Comedy", "Music",
    "Podcasts & Interviews",
    # News & politics
    "News", "Politics", "World Affairs",
    # Lifestyle
    "Health & Wellness", "Fitness", "Cooking & Food", "Travel",
    "Fashion & Beauty", "Home & DIY",
    # Sports, reviews, misc
    "Sports", "Product Reviews", "Documentary", "Commentary & Opinion",
    "Other",
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


def _bucket_transcript_by_time(
    segments: list[dict] | None, bucket_seconds: int = 45, max_chars: int = 20_000
) -> str:
    """
    Collapse per-line transcript snippets into coarse time buckets (e.g.
    "[03:15] ...") so the model has real timestamps to anchor an inferred
    section outline to, without blowing up the prompt with every caption
    line's own timestamp.
    """
    if not segments:
        return ""

    buckets: dict[int, list[str]] = {}
    for seg in segments:
        bucket_start = int(seg["start"] // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_start, []).append(seg["text"])

    lines = []
    total = 0
    for bucket_start in sorted(buckets):
        mm, ss = divmod(bucket_start, 60)
        line = f"[{mm:02d}:{ss:02d}] {' '.join(buckets[bucket_start])}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


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
    transcript_segments: list[dict] | None = None,
) -> str:
    category_list = ", ".join(CATEGORIES)
    structure = _structure_instructions(length, format)

    header_lines = [
        f"CATEGORY: <pick the SINGLE most specific matching label from: {category_list}. "
        "Prefer the narrowest label that genuinely fits over a broader nearby one — e.g. "
        "a video about a specific programming language or framework is 'Software Development', "
        "not 'Other'; a video about budgeting or saving is 'Personal Finance', not 'Business'. "
        "Only use 'Other' if nothing on the list is a reasonable fit.>"
    ]

    if title:
        header_lines.append(
            f'ANSWER: <if the title "{title}" poses a question or promises to reveal/explain '
            "something, 1-2 sentences directly resolving it based on the transcript. "
            "If the title isn't a question or claim, write NONE>"
        )

    has_declared_chapters = _looks_like_it_has_timestamps(description, comments)
    timestamped_transcript = ""
    if has_declared_chapters:
        header_lines.append(
            "TIMESTAMPS: <the description or comments below appear to contain chapter "
            "timestamps (MM:SS style). Extract ONLY the ones explicitly written there, output "
            "each on its own line right after this one, formatted exactly 'MM:SS | Label' (or "
            "H:MM:SS for longer videos) — one chapter per line, no blank lines between them, "
            "then immediately the '---' line. Do not add, guess, or infer any timestamp that "
            "isn't literally written in the description/comments below.>"
        )
    elif transcript_segments:
        timestamped_transcript = _bucket_transcript_by_time(transcript_segments)
        if timestamped_transcript:
            header_lines.append(
                "TIMESTAMPS: <using the TIMESTAMPED TRANSCRIPT below, break the video into its "
                "main sections by topic change. Output each as its own line right after this "
                "one, formatted exactly 'MM:SS | short label' (2-6 words, no ending punctuation) "
                "— one section per line, no blank lines between them, then immediately the '---' "
                "line. Aim for roughly one section every 2-4 minutes of runtime; a short or "
                "single-topic video can have as few as 2-3 sections — don't force more than the "
                "content naturally has. Use only timestamps that appear in the TIMESTAMPED "
                "TRANSCRIPT below.>"
            )

    if comments:
        header_lines.append(
            "SENTIMENT: <one of Positive, Mostly Positive, Mixed, Mostly Negative, Negative> "
            "— <1-2 sentence blurb on common themes in the comments>"
        )
        header_lines.append(
            "HIGHLIGHT: <scan the comments below for the strongest, most substantive praise or "
            "recurring positive theme — what viewers valued most. 1-2 sentences summarizing it. "
            "If comments are overwhelmingly critical with nothing notable to praise, write NONE. "
            "Do not invent praise that isn't actually present in the comments.>"
        )
        header_lines.append(
            "COUNTERPOINT: <scan ALL the comments below (not just the highest-liked) for the "
            "strongest substantive criticism, disagreement, correction, or counterargument to "
            "the video's claims — the 'other side' someone deciding whether to trust this video "
            "would want to know about. 1-2 sentences summarizing it. If the comments raise no "
            "real criticism (just praise, jokes, or unrelated chatter), write NONE. Do not "
            "invent a counterpoint that isn't actually present in the comments.>"
        )

    header_lines.append(
        "KEY_POINTS: <break the video's content into 4-8 key points. Output each on its own "
        "line right after this one, formatted EXACTLY 'short one-sentence point (≤20 words) "
        ":: a deeper 2-3 sentence elaboration with specifics/examples from the transcript' — "
        "use ' :: ' as the exact separator between the two, one point per line, no blank lines "
        "between them, then immediately the '---' line. The short parts alone should give "
        "someone the full gist in 10 seconds of reading; the elaborations are for someone who "
        "wants more on that specific point.>"
    )

    header_lines.append(
        "GLOSSARY: <list every acronym, piece of jargon, specialized term, or named "
        "product/program/feature used in the video that a general audience likely wouldn't "
        "already know — this includes technical terms (e.g. 'RAG', 'hypertrophy'), business/"
        "finance jargon (e.g. 'CAC', 'quantitative easing'), AND specific named things like "
        "product features, company initiatives, or proprietary programs mentioned by name "
        "(e.g. 'Pay Per Crawl', 'Vision Pro', a lesser-known law or protocol) — don't limit "
        "yourself to only acronyms. When in doubt about whether a term is common knowledge, "
        "include it. Skip only truly everyday words everyone knows. Output each on its own "
        "line right after this one, formatted EXACTLY 'TERM :: a one-sentence definition :: a "
        "short example sentence using the term the way THIS video used it' — use ' :: ' as the "
        "exact separator between all three parts, one term per line, no blank lines between "
        "them, then immediately the '---' line. If the video truly uses no notable terms, "
        "write NONE immediately after the colon on this same line instead of a list.>"
    )

    header_lines.append("---")
    header = "\n".join(header_lines)

    extra_context = ""
    if description:
        extra_context += (
            "\n\nVIDEO DESCRIPTION (use only to find chapter timestamps if present, "
            f"not for the summary itself):\n{description[:3000]}"
        )
    if timestamped_transcript:
        extra_context += (
            "\n\nTIMESTAMPED TRANSCRIPT (use only to determine section timestamps, "
            f"not for the summary itself):\n{timestamped_transcript}"
        )
    if comments:
        comment_lines = "\n".join(f'[{c["like_count"]} likes] "{c["text"]}"' for c in comments)
        extra_context += (
            "\n\nTOP COMMENTS (shown with like counts — weight your overall sentiment "
            "assessment toward the higher-liked ones, but read every comment when looking "
            f"for a highlight or counterpoint since praise/criticism aren't always the "
            f"most-liked; also check these for timestamp callouts):\n{comment_lines}"
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


_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _strip_markdown(text: str | None) -> str | None:
    """
    Models occasionally slip markdown bold (**word**) into free-text
    fields even when not asked for it — neither frontend renders
    markdown, so it would otherwise show up as literal asterisks.
    Strips the ** markers while keeping the wrapped text.
    """
    if not text:
        return text
    return _MARKDOWN_BOLD_RE.sub(r"\1", text)


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
    highlight = None
    counterpoint = None
    chapters = []
    key_points = []
    glossary = []
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
        elif upper.startswith("HIGHLIGHT:"):
            val = stripped.split(":", 1)[1].strip()
            highlight = None if not val or val.upper() == "NONE" else val
            idx += 1
        elif upper.startswith("COUNTERPOINT:"):
            val = stripped.split(":", 1)[1].strip()
            counterpoint = None if not val or val.upper() == "NONE" else val
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
        elif upper.startswith("KEY_POINTS:"):
            idx += 1
            while idx < len(lines):
                sub = lines[idx].strip().replace("**", "").lstrip("-*# ").strip()
                if not sub or sub.upper() == "---" or "::" not in sub:
                    break
                point_part, detail_part = sub.split("::", 1)
                point_part = point_part.strip()
                detail_part = detail_part.strip()
                if point_part:
                    key_points.append({"point": point_part, "detail": detail_part})
                idx += 1
        elif upper.startswith("GLOSSARY:"):
            idx += 1
            while idx < len(lines):
                sub = lines[idx].strip().replace("**", "").lstrip("-*# ").strip()
                if not sub or sub.upper() == "---" or sub.count("::") < 2:
                    break
                term_part, definition_part, example_part = sub.split("::", 2)
                term_part = term_part.strip()
                definition_part = definition_part.strip()
                example_part = example_part.strip()
                if term_part:
                    glossary.append({"term": term_part, "definition": definition_part, "example": example_part})
                idx += 1
        else:
            break  # unrecognized line -> start of summary body

    summary = "\n".join(lines[idx:]).strip()
    key_points = [
        {"point": _strip_markdown(kp["point"]), "detail": _strip_markdown(kp["detail"])}
        for kp in key_points
    ]
    glossary = [
        {
            "term": g["term"],
            "definition": _strip_markdown(g["definition"]),
            "example": _strip_markdown(g["example"]),
        }
        for g in glossary
    ]
    return {
        "category": category,
        "summary": _strip_markdown(summary),
        "answer": _strip_markdown(answer),
        "sentiment_label": sentiment_label,
        "sentiment_blurb": _strip_markdown(sentiment_blurb),
        "highlight": _strip_markdown(highlight),
        "counterpoint": _strip_markdown(counterpoint),
        "chapters": chapters,
        "key_points": key_points,
        "glossary": glossary,
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
    transcript_segments: list[dict] | None = None,
) -> dict:
    """
    Summarize a transcript with the chosen provider. Returns
    {"summary", "category", "answer", "sentiment_label", "sentiment_blurb",
    "highlight", "counterpoint", "chapters", "key_points", "glossary"}.
    The optional fields are None/[] unless their inputs were provided.
    Raises SummarizerError on failure.
    """
    if not transcript_text.strip():
        raise SummarizerError("Empty transcript — nothing to summarize.")

    truncated = transcript_text[:100_000]
    prompt = build_prompt(
        truncated, length=length, format=format, comments=comments,
        title=title, description=description, transcript_segments=transcript_segments,
    )

    raw = _call_provider(provider, prompt, client=client)
    if not raw:
        raise SummarizerError("The model returned an empty response.")

    parsed = _parse_response(raw)
    if not parsed["summary"]:
        raise SummarizerError("The model returned an empty response.")
    return parsed
