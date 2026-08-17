"""ORM models."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    summary_length: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    digest_email_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The user's own top-level categories, as a JSON list. These LAYER OVER
    # the built-in vocabulary in summarizer.CATEGORIES rather than replacing
    # it: the model prefers one of these for the primary label, and falls
    # back to the built-ins when none genuinely fits. NULL means "no custom
    # list yet", which behaves exactly as before.
    custom_categories_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    summaries: Mapped[list["Summary"]] = relationship(back_populates="user")
    api_tokens: Mapped[list["ApiToken"]] = relationship(back_populates="user")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="api_tokens")


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Holds a comma-joined list of labels ("Gadgets & Consumer Tech,
    # Startups & Tech Business, Product Reviews"), not a single one — at
    # String(64) any 3-label result overflowed and killed the whole save
    # with a StringDataRightTruncation, which is what made summarizing
    # fail for most videos.
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscriber_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Channel-level stats (lifetime views, upload count, averages) as JSON
    # so new fields don't each need their own migration.
    channel_stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sentiment_blurb: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-category comment counts and like totals, e.g.
    # {"positive": {"count": 12, "likes": 3400}, ...}. JSON rather than six
    # columns so the shape can grow without another migration.
    comment_tally_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    highlight: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    glossary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-text topic tags ("Proxmox", "RAG") — the fine-grained signal
    # under the coarse category. Not drawn from any fixed vocabulary.
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The user's own answers to the Questions tab, e.g.
    # {"worth_it": "definitely", "novelty": "some_new"}. This is
    # explicit preference data that can't be reconstructed after the
    # fact, which is why it's captured at read time.
    feedback_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # An honest replacement title, set only when the real one materially
    # misrepresents the video. NULL means the original was fine.
    true_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    playlist_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playlist_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship(back_populates="summaries")


class PendingSummary(Base):
    """
    A row per in-flight /summarize/queue job — created when the job is
    scheduled, removed when it finishes successfully (the real Summary
    row exists by then), updated to status='failed' if it errors out.
    Lets the UI show what's currently processing instead of a job just
    silently vanishing into a background task.
    """
    __tablename__ = "pending_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="processing", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CustomGlossaryTerm(Base):
    __tablename__ = "custom_glossary_terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    term: Mapped[str] = mapped_column(String(255), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    example: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
