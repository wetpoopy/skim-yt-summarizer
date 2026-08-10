"""
Daily digest email — one per user, summarizing what they skimmed in
the last 24 hours. Runs on a schedule (see app/main.py's startup).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.email import EmailSendError, send_email
from app.models import Summary, User

APP_URL = "https://www.skimstash.com"


def _build_digest_text(summaries: list[Summary]) -> str:
    lines = [f"You skimmed {len(summaries)} video{'s' if len(summaries) != 1 else ''} in the last 24 hours:", ""]
    for s in summaries:
        title = s.title or s.video_id
        tldr = s.summary_text.strip().split("\n")[0].lstrip("-*# ").strip()
        lines.append(f"• {title}")
        if tldr:
            lines.append(f"  {tldr}")
        lines.append(f"  {APP_URL}")
        lines.append("")
    return "\n".join(lines)


def send_daily_digests(db: Session) -> int:
    """Sends today's digest to every opted-in user with activity in the last 24h. Returns count sent."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    users = db.scalars(select(User).where(User.digest_email_enabled != False)).all()  # noqa: E712

    sent = 0
    for user in users:
        summaries = db.scalars(
            select(Summary)
            .where(Summary.user_id == user.id, Summary.created_at >= cutoff)
            .order_by(Summary.created_at.desc())
        ).all()
        if not summaries:
            continue

        try:
            send_email(
                to=user.email,
                subject=f"Your Skim digest — {len(summaries)} video{'s' if len(summaries) != 1 else ''}",
                text=_build_digest_text(summaries),
            )
            sent += 1
        except EmailSendError:
            continue  # one user's failed email shouldn't block the rest of the run

    return sent
