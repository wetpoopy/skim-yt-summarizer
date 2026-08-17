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
MAX_TOKENS = 8192


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
    "Software Development", "AI & Machine Learning", "Agents", "API", "Cybersecurity",
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

_RUNESCAPE_SIGNAL_RE = re.compile(r"\brunescape\b|\bosrs\b|\bjagex\b", re.IGNORECASE)
_API_TITLE_RE = re.compile(r"\bapi\b", re.IGNORECASE)

# Must stay <= the Summary.category column width in app/models.py.
MAX_CATEGORY_LEN = 255


def normalize_category(
    raw_category: str,
    title: str | None = None,
    extra_text: str = "",
    user_categories: list[str] | None = None,
) -> str:
    """
    A deterministic safety net on top of the CATEGORY prompt instructions,
    not a replacement for them: drops any label the model invented outside
    CATEGORIES (nothing stops it hallucinating one), and force-applies rules
    that are cheap to guarantee exactly rather than hope the model
    remembers every time. Precedence when multiple rules fire on the same
    video (each inserted after the last, so the last one applied ends up
    primary): 'Agents' if the title says so, then 'API' if the title says
    so (API outranks Agents when both are in the title), then 'Runescape'
    if OSRS-specific terms show up anywhere — always the primary, full stop.
    """
    # The user's own labels are accepted alongside the built-ins, and win a
    # case collision so their spelling is what gets stored.
    user_categories = [c.strip() for c in (user_categories or []) if c and c.strip()]
    valid = {c.lower(): c for c in CATEGORIES}
    valid.update({c.lower(): c for c in user_categories})

    cats = []
    for part in raw_category.split(","):
        canonical = valid.get(part.strip().lower())
        if canonical and canonical not in cats:
            cats.append(canonical)
    if not cats:
        cats = ["Other"]

    # Promote one of the user's own labels to primary if the model picked any
    # of them. They asked to own the top level, so a custom label outranks a
    # built-in that happens to have been listed first.
    if user_categories:
        owned = {c.lower() for c in user_categories}
        mine = [c for c in cats if c.lower() in owned]
        if mine:
            cats = mine + [c for c in cats if c.lower() not in owned]

    title_lower = (title or "").lower()
    if "agent" in title_lower:
        cats = [c for c in cats if c != "Agents"]
        cats.insert(0, "Agents")

    if _API_TITLE_RE.search(title_lower):
        cats = [c for c in cats if c != "API"]
        cats.insert(0, "API")

    if _RUNESCAPE_SIGNAL_RE.search(f"{title or ''} {extra_text}"):
        cats = [c for c in cats if c != "Runescape"]
        cats.insert(0, "Runescape")

    # Never emit more than the category column can hold. Labels are ordered
    # most-significant-first by the rules above, so dropping from the tail
    # loses the least important ones. Losing a trailing label is a far better
    # outcome than the DB write blowing up and taking the whole summary with
    # it, which is exactly what used to happen.
    while len(", ".join(cats)) > MAX_CATEGORY_LEN and len(cats) > 1:
        cats.pop()

    return ", ".join(cats)[:MAX_CATEGORY_LEN]


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
            "- One opening bullet giving the overall gist (no label like 'TL;DR' or 'Summary' in front of it)\n"
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

    # mixed (default) — opening overview + bullets + takeaway
    overview = "a 2-3 sentence overview" if length == "detailed" else "a one-sentence overview"
    takeaway = (
        "" if length == "brief"
        else ", ending with one extra bullet for anything notably actionable, surprising, or a key takeaway"
    )
    return (
        f"Open with {overview} as a plain sentence — no numbering, no bullet, no bold label, no "
        "leading list marker, and do NOT prefix it with any header word like 'TL;DR', 'Summary', "
        "or 'Overview' — just start directly with the sentence itself. Then a blank line, then "
        f"bullet points (using '-') covering the main content ({lo}-{hi} bullets){takeaway}."
    )


def build_prompt(
    transcript_text: str,
    length: str = "standard",
    format: str = "mixed",
    comments: list[dict] | None = None,
    title: str | None = None,
    description: str | None = None,
    transcript_segments: list[dict] | None = None,
    known_tags: list[str] | None = None,
    user_categories: list[str] | None = None,
) -> str:
    category_list = ", ".join(CATEGORIES)
    structure = _structure_instructions(length, format)

    # The user's own top-level categories LAYER OVER the built-ins rather
    # than replacing them: prefer theirs for the primary label, fall back to
    # the standard vocabulary when none of theirs genuinely fits. That keeps
    # their shelf labels stable without leaving unusual videos uncategorised.
    user_categories = [c.strip() for c in (user_categories or []) if c and c.strip()]
    own_rule = ""
    if user_categories:
        own_rule = (
            "IMPORTANT — these are the user's OWN top-level categories and take priority: "
            f"{', '.join(user_categories)}. If any one of them genuinely fits this video, it MUST "
            "be the FIRST label you list. Only fall back to the general list below for the primary "
            "label when none of the user's categories is a reasonable fit. You may still add "
            "general labels after theirs. Do not invent variations on the user's labels — use them "
            "verbatim or not at all. "
        )

    header_lines = [
        f"CATEGORY: <{own_rule}pick EVERY label from this list that genuinely applies: {category_list}. "
        "Most videos fit 1-2 labels, occasionally 3 — list only ones that are truly relevant, "
        "MOST-SPECIFIC FIRST, separated by ', '. The first label you list becomes this video's "
        "primary category, so ordering matters: 'Runescape' always goes first when it applies — "
        "this includes any video substantially about RuneScape/Old School RuneScape content even "
        "if the word 'RuneScape' is never said outright (e.g. OSRS bosses, minigames, quests, "
        "skilling, the Grand Exchange, Jagex, or other OSRS-specific slang); 'API' comes before "
        "'Agents' when both apply, and 'Agents' is more specific than 'AI & Machine Learning' and "
        "should come first when both of those apply; in general, "
        "prefer the narrowest label that genuinely fits over a broader nearby one — e.g. a video "
        "about a specific programming language or framework is 'Software Development', not "
        "'Other'; a video about budgeting or saving is 'Personal Finance', not 'Business'. Only "
        "use 'Other' if nothing on the list is a reasonable fit, and never pair 'Other' with "
        "another label.>"
    ]

    # Always produced, for every video. This used to be conditional on the
    # title being a question ("write NONE if it isn't"), which meant a
    # video called "Obsidian Properties Full Breakdown" got no quick take
    # at all while "How To Master N8N API Calls" did — inconsistent for no
    # reason the reader can see. Now it's the one-glance takeaway on every
    # summary; it just answers the title's question when there is one.
    # Deliberately hard-capped and stripped of hedging: this is the
    # one-glance line, and every wasted word costs it. Density over
    # completeness — the key points below carry the detail.
    answer_rules = (
        "ONE sentence, 25 words maximum. Maximum information per word. "
        "Start with the substance — no lead-ins like 'This video explains', 'The video covers', "
        "'In this video', or 'The speaker argues'. No hedging ('essentially', 'basically', "
        "'arguably', 'it seems'). No filler adjectives. State the specific finding, number, "
        "method, or conclusion rather than describing that one exists. "
        "Never write NONE — always produce this."
    )
    if title:
        header_lines.append(
            f'ANSWER: <the single most useful thing to know from this video. If the title '
            f'"{title}" asks a question or promises something, answer it outright. Otherwise '
            f"state the video's core claim or finding. {answer_rules}>"
        )
    else:
        header_lines.append(f"ANSWER: <the single most useful thing to know from this video. {answer_rules}>")

    if title:
        # The one test is MISREPRESENTATION — a gap between what the title
        # claims and what the transcript delivers. Explicitly not a test of
        # how informative the title is: an earlier version also caught
        # "vague" titles, which renamed things like "Me at the zoo" that
        # are perfectly accurate, just terse.
        header_lines.append(
            f'TRUE_TITLE: <compare the title "{title}" against what the transcript actually '
            "delivers. Write a replacement ONLY if a viewer would feel MISLED after watching — "
            "that is, the title claims something the video does not deliver: it promises a "
            "reveal/answer/method the video never gives; states a result, number, or scale the "
            "content doesn't support; is mostly about a different subject than advertised; or "
            "asks a question it never answers. "
            "In EVERY other case write NONE. Specifically, these are NOT misleading and must "
            "get NONE: a title that is vague, terse, plain, or uninformative but accurate; a "
            "punchy, enthusiastic, or dramatic title whose claim the video does back up; a "
            "title that undersells the content; unusual capitalization or styling. Being "
            "unhelpful is not the same as being dishonest — only dishonesty gets renamed, and "
            "the large majority of titles should get NONE. "
            "When you do replace it: 4-12 words stating plainly what the video actually is. "
            "No hype, no question marks, no ALL CAPS.>"
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
            "SENTIMENT: <one of Positive, Mostly Positive, Mostly Negative, Negative — pick "
            "whichever side the comments lean toward overall, even if opinion is genuinely "
            "split; never answer with a neutral/'mixed' label, always lean one way> "
            "— <ONE sentence, 20 words max, naming the single most common specific theme. "
            "No lead-ins like 'Commenters say' or 'Viewers found' — start with the substance. "
            "Say what they actually praised or objected to, not that they praised or objected.>"
        )
        header_lines.append(
            "HIGHLIGHT: <the strongest specific praise in the comments — what viewers valued "
            "most, concretely. ONE sentence, 20 words max, no lead-in phrases, no hedging. "
            "If comments are overwhelmingly critical with nothing notable to praise, write NONE. "
            "Do not invent praise that isn't actually present in the comments.>"
        )
        header_lines.append(
            "COMMENT_SENTIMENT: <classify EVERY numbered comment below. Output one line per "
            "comment right after this one, formatted EXACTLY 'N: positive' / 'N: negative' / "
            "'N: neutral' where N is that comment's number — one per line, no blank lines "
            "between them, no extra commentary, then immediately the '---' line. Use 'neutral' "
            "only for comments that genuinely express no opinion about the video (questions, "
            "timestamps, off-topic chatter). Judge sentiment toward the VIDEO/creator, not the "
            "subject matter — someone angry about the topic but praising the explanation is "
            "positive.>"
        )
        header_lines.append(
            "COUNTERPOINT: <scan ALL the comments below (not just the highest-liked) for the "
            "strongest substantive criticism, correction, or counterargument to the video's "
            "claims — what someone deciding whether to trust this video would want to know. "
            "ONE sentence, 20 words max, no lead-in phrases, no hedging. State the actual "
            "objection, not that an objection exists. If the comments raise no real criticism "
            "(just praise, jokes, or unrelated chatter), write NONE. Do not invent a "
            "counterpoint that isn't actually present in the comments.>"
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

    # Free-text, deliberately NOT constrained to a fixed vocabulary. The
    # CATEGORY list above is the coarse, human-facing shelf; these are the
    # fine-grained signal ("Proxmox", "RAG", "OSRS ironman") that a fixed
    # list could never keep up with — every gap in one previously meant a
    # code change. Existing tags are offered as a reuse hint so the set
    # stays consistent without being hardcoded.
    reuse_hint = ""
    if known_tags:
        reuse_hint = (
            " You have used these tags before — REUSE an existing one whenever it genuinely "
            f"fits rather than coining a near-duplicate: {', '.join(known_tags[:60])}."
        )
    header_lines.append(
        "TAGS: <3-6 specific topic tags for this video, comma-separated on this same line. "
        "Name the concrete tools, technologies, games, people, or subjects actually discussed "
        "(e.g. 'Proxmox', 'Docker', 'RAG', 'Old School RuneScape', 'index funds') — not broad "
        "genres, which the CATEGORY line already covers. Title Case. No hashtags, no "
        "duplicates of each other."
        + reuse_hint
        + ">"
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
        # Numbered so COMMENT_SENTIMENT can refer to each one by index —
        # the model classifies, but the counting and like-summing is done
        # in Python from these same like counts, never by the model.
        comment_lines = "\n".join(
            f'{i + 1}. [{c["like_count"]} likes] "{c["text"]}"' for i, c in enumerate(comments)
        )
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


_LEADING_LABEL_RE = re.compile(r"^\s*(?:\*\*)?\s*(?:TL;?DR|TLDR|SUMMARY|OVERVIEW)\s*:?\s*(?:\*\*)?\s*[:\-—]?\s*", re.IGNORECASE)


def _strip_leading_label(summary: str) -> str:
    """
    Models sometimes ignore the "no header word" instruction and open with
    a literal "TL;DR:" (or "Summary:"/"Overview:") label anyway — strip it
    defensively so the frontend never has to display it.
    """
    return _LEADING_LABEL_RE.sub("", summary, count=1)


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
    true_title = None
    tags: list[str] = []
    sentiment_label = None
    sentiment_blurb = None
    highlight = None
    counterpoint = None
    chapters = []
    key_points = []
    glossary = []
    comment_sentiments: dict[int, str] = {}
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
        elif upper.startswith("TAGS:"):
            raw_tags = stripped.split(":", 1)[1]
            for part in raw_tags.split(","):
                tag = part.strip().strip('"').lstrip("#").strip()
                if tag and tag.upper() != "NONE" and tag not in tags:
                    tags.append(tag)
            idx += 1
        elif upper.startswith("TRUE_TITLE:"):
            val = stripped.split(":", 1)[1].strip().strip('"')
            true_title = None if not val or val.upper() == "NONE" else val
            idx += 1
        elif upper.startswith("COMMENT_SENTIMENT:"):
            idx += 1
            while idx < len(lines):
                sub = lines[idx].strip().replace("**", "").lstrip("-*# ").strip()
                if not sub or sub.upper() == "---" or ":" not in sub:
                    break
                num_part, label_part = sub.split(":", 1)
                label = label_part.strip().lower()
                try:
                    num = int(num_part.strip().rstrip("."))
                except ValueError:
                    break
                if label in ("positive", "negative", "neutral"):
                    comment_sentiments[num] = label
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
    summary = _strip_leading_label(summary)
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
        "true_title": _strip_markdown(true_title),
        "tags": tags[:8],
        "sentiment_label": sentiment_label,
        "sentiment_blurb": _strip_markdown(sentiment_blurb),
        "highlight": _strip_markdown(highlight),
        "counterpoint": _strip_markdown(counterpoint),
        "chapters": chapters,
        "key_points": key_points,
        "glossary": glossary,
        "comment_sentiments": comment_sentiments,
    }


def tally_comment_sentiment(comments: list[dict] | None, sentiments: dict[int, str]) -> dict | None:
    """
    Turn the model's per-comment labels into counts and like totals.

    The arithmetic deliberately happens here rather than asking the model
    for totals — models are unreliable at summing dozens of numbers, and
    we already hold the exact like counts that came back from YouTube.
    The model is only used for the part it's actually good at (judging
    tone); every number below is computed from real data.

    Comment numbers are 1-based in the prompt. Anything the model failed
    to classify, or invented an index for, is simply skipped rather than
    guessed at.
    """
    if not comments or not sentiments:
        return None

    tally = {
        "positive": {"count": 0, "likes": 0},
        "negative": {"count": 0, "likes": 0},
        "neutral": {"count": 0, "likes": 0},
    }
    classified = 0
    for num, label in sentiments.items():
        if label not in tally or not (1 <= num <= len(comments)):
            continue
        tally[label]["count"] += 1
        tally[label]["likes"] += comments[num - 1].get("like_count") or 0
        classified += 1

    if not classified:
        return None
    tally["total_classified"] = classified
    tally["total_comments_sampled"] = len(comments)
    return tally


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
    known_tags: list[str] | None = None,
    user_categories: list[str] | None = None,
) -> dict:
    """
    Summarize a transcript with the chosen provider. Returns
    {"summary", "category", "answer", "sentiment_label", "sentiment_blurb",
    "highlight", "counterpoint", "chapters", "key_points", "glossary",
    "comment_tally"}.
    The optional fields are None/[] unless their inputs were provided.
    Raises SummarizerError on failure.
    """
    if not transcript_text.strip():
        raise SummarizerError("Empty transcript — nothing to summarize.")

    truncated = transcript_text[:100_000]
    prompt = build_prompt(
        truncated, length=length, format=format, comments=comments,
        title=title, description=description, transcript_segments=transcript_segments,
        known_tags=known_tags, user_categories=user_categories,
    )

    raw = _call_provider(provider, prompt, client=client)
    if not raw:
        raise SummarizerError("The model returned an empty response.")

    parsed = _parse_response(raw)
    if not parsed["summary"]:
        raise SummarizerError("The model returned an empty response.")
    if not parsed["key_points"]:
        # The model doesn't always emit a well-formed KEY_POINTS section
        # (wrong delimiter, skipped it, truncation) — without this, that
        # video would silently lose the SHORT view entirely and only ever
        # show LONG, with no toggle. Guarantee SHORT always has something.
        parsed["key_points"] = _synthesize_key_points(parsed["summary"])
    parsed["comment_tally"] = tally_comment_sentiment(comments, parsed.pop("comment_sentiments", {}))
    return parsed


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _synthesize_key_points(summary: str) -> list[dict]:
    lines = [line.strip().lstrip("-*•").strip() for line in summary.split("\n")]
    bullets = [line for line in lines if line]
    if len(bullets) < 2:
        # Prose-format summary, no bullet lines to split on — fall back
        # to sentences instead.
        bullets = [s.strip() for s in _SENTENCE_SPLIT_RE.split(summary) if s.strip()]
    bullets = bullets[:8]
    return [
        {"point": b if len(b) <= 140 else b[:137] + "...", "detail": b}
        for b in bullets
    ]


def define_terms(terms: list[str], client: Anthropic | None = None, provider: str = "anthropic") -> list[dict]:
    """
    Defines a user-supplied batch of glossary terms in a single LLM call,
    regardless of how many terms are in the batch — one round-trip whether
    it's 1 term or 20. Returns [{"term", "definition", "example"}, ...].
    """
    terms = [t.strip() for t in terms if t.strip()]
    if not terms:
        return []

    term_list = "; ".join(terms)
    prompt = (
        "For each of the following terms, write a general-purpose one-sentence "
        "definition (not tied to any specific video) and a short example sentence "
        "showing the term used in context. Output each on its own line, formatted "
        "EXACTLY 'TERM :: a one-sentence definition :: a short example sentence "
        "using the term' — use ' :: ' as the exact separator between all three "
        "parts, one term per line, no blank lines between them, no numbering, no "
        "commentary before or after the list.\n\n"
        f"Terms: {term_list}"
    )

    raw = _call_provider(provider, prompt, client=client)
    if not raw:
        raise SummarizerError("The model returned an empty response.")

    results = []
    for line in raw.split("\n"):
        line = line.strip().replace("**", "").lstrip("-*# ").strip()
        if not line or line.count("::") < 2:
            continue
        term_part, definition_part, example_part = line.split("::", 2)
        term_part = term_part.strip()
        if term_part:
            results.append({
                "term": term_part,
                "definition": _strip_markdown(definition_part.strip()),
                "example": _strip_markdown(example_part.strip()),
            })
    return results
