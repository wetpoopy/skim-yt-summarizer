"""
Summarization via the Claude API.

Kept deliberately simple for now — one prompt, one call. Swap out
build_prompt() later to add formats (bullets/TLDR/chapters), tone
controls, etc. without touching the rest of the pipeline.
"""

import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_TRANSCRIPT_CHARS = 100_000  # rough safety cap before we bother chunking


class SummarizerError(Exception):
    pass


def _get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SummarizerError("Server is missing ANTHROPIC_API_KEY.")
    return Anthropic(api_key=api_key)


CATEGORIES = [
    "Tech", "Education", "Entertainment", "News", "Business",
    "Health", "Gaming", "Music", "Other",
]


def build_prompt(transcript_text: str) -> str:
    """Basic summarization prompt. Refine this later."""
    category_list = ", ".join(CATEGORIES)
    return (
        "Summarize the following YouTube video transcript for someone deciding "
        "whether to watch it and/or trying to get the key points quickly.\n\n"
        "Respond in exactly this format:\n"
        f"CATEGORY: <pick the single best-fitting label from: {category_list}>\n\n"
        "1. One-sentence TL;DR\n"
        "2. 4-8 bullet points covering the main content\n"
        "3. Anything notably actionable, surprising, or a key takeaway\n\n"
        "Be concise. Don't pad. Don't restate the instructions.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )


def _split_category(raw_text: str) -> tuple[str, str]:
    """Pull the 'CATEGORY: X' first line off the model's response."""
    lines = raw_text.split("\n", 1)
    first_line = lines[0].strip()
    if first_line.upper().startswith("CATEGORY:"):
        category = first_line.split(":", 1)[1].strip() or "Other"
        rest = lines[1].strip() if len(lines) > 1 else ""
        return category, rest
    return "Other", raw_text.strip()


def summarize(transcript_text: str, client: Anthropic | None = None) -> dict:
    """
    Summarize a transcript with Claude. Returns {"summary": str, "category": str}.
    Raises SummarizerError on failure.
    """
    if not transcript_text.strip():
        raise SummarizerError("Empty transcript — nothing to summarize.")

    truncated = transcript_text[:MAX_TRANSCRIPT_CHARS]
    client = client or _get_client()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": build_prompt(truncated)}],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        raw = "\n".join(text_blocks).strip()
        if not raw:
            raise SummarizerError("Claude returned an empty response.")
        category, summary = _split_category(raw)
        if not summary:
            raise SummarizerError("Claude returned an empty response.")
        return {"summary": summary, "category": category}
    except SummarizerError:
        raise
    except Exception as e:
        raise SummarizerError(f"Summarization failed ({e.__class__.__name__}): {e}")
