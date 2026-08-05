from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from clock import utcnow_naive
from config import DATABASE_URL

# Postgres only (config.py rejects anything else). pool_pre_ping because the
# server is over a socket and connections go stale; tests build their own engine
# and never reach here.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class JournalEntry(SQLModel, table=True):
    # Dedup is per-user: identical text from two accounts hashes the same, so a
    # global UNIQUE(content_hash) would drop the second user's entry. The hash
    # stays a pure content fingerprint; user_id is the scoping dimension.
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_user_content"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    # Owner (ADR 0003). NOT NULL as of the Postgres migration: every entry has an
    # owner. Legacy unowned rows were backfilled (scripts/backfill_owner.py) before
    # the SQLite→Postgres data copy, so no NULL survives. Ingest paths must stamp a
    # user_id; the user-less startup sync assigns the Keep account's owner.
    user_id: int = Field(foreign_key="users.id", index=True)
    source: str  # "google_keep" | "goodnotes" | "text"
    content: str
    timestamp: datetime = Field(default_factory=utcnow_naive)  # when the entry was authored
    mood_score: Optional[float] = None  # 0.0 (very negative) – 1.0 (very positive)
    # One-sentence explanation of the mood score, from the same LLM call
    mood_reasoning: Optional[str] = None
    content_hash: str = Field(index=True)
    # When the entry entered this system; NULL for rows predating the field
    imported_at: Optional[datetime] = Field(default_factory=utcnow_naive)
    # ID of the note at its source (Keep note id → keep.google.com/#NOTE/<id>)
    external_id: Optional[str] = None


def get_session() -> Generator:
    with Session(engine) as session:
        yield session


def user_entries(user_id: int):
    """Base query for everything a user can read — the isolation choke point
    (ADR 0003). Every read path starts from this SELECT and chains its own
    filters, so `WHERE user_id = ?` is written exactly once. Since the Postgres
    migration `user_id` is NOT NULL — every entry has an owner, so there are no
    unowned rows to leak.
    """
    return select(JournalEntry).where(JournalEntry.user_id == user_id)
