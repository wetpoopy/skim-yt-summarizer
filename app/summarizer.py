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


def build_prompt(transcript_text: str) -> str:
    """Basic summarization prompt. Refine this later."""
    return (
        "Summarize the following YouTube video transcript for someone deciding "
        "whether to watch it and/or trying to get the key points quickly.\n\n"
        "Structure your response as:\n"
        "1. One-sentence TL;DR\n"
        "2. 4-8 bullet points covering the main content\n"
        "3. Anything notably actionable, surprising, or a key takeaway\n\n"
        "Be concise. Don't pad. Don't restate the instructions.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )


def summarize(transcript_text: str, client: Anthropic | None = None) -> str:
    """
    Summarize a transcript with Claude. Returns the summary text.
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
        summary = "\n".join(text_blocks).strip()
        if not summary:
            raise SummarizerError("Claude returned an empty response.")
        return summary
    except SummarizerError:
        raise
    except Exception as e:
        raise SummarizerError(f"Summarization failed ({e.__class__.__name__}): {e}")
