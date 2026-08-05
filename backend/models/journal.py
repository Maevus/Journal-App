from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from clock import utcnow_naive
from config import DATABASE_URL

# Postgres only (config.py rejects anything else); pool_pre_ping since
# connections go stale over the socket. Tests build their own engine, never this one.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class JournalEntry(SQLModel, table=True):
    # Dedup is per-user: a global UNIQUE(content_hash) would drop a second
    # user's identical entry, so user_id scopes it and the hash stays a pure fingerprint.
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_user_content"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    # Owner (ADR 0003), NOT NULL since the Postgres migration — legacy rows were
    # backfilled (scripts/backfill_owner.py) so none survive unowned. Ingest paths
    # must stamp a user_id; the user-less startup sync assigns the Keep account's.
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
    (ADR 0003). Every read path chains its own filters onto this SELECT, so
    `WHERE user_id = ?` is written exactly once. `user_id` is NOT NULL, so
    there are no unowned rows to leak.
    """
    return select(JournalEntry).where(JournalEntry.user_id == user_id)
