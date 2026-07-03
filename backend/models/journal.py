from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from clock import utcnow_naive
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class JournalEntry(SQLModel, table=True):
    # Dedup is per-user: identical text from two accounts hashes the same, so a
    # global UNIQUE(content_hash) would drop the second user's entry. The hash
    # stays a pure content fingerprint; user_id is the scoping dimension.
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_user_content"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    # Owner (ADR 0003). NULL = legacy rows from before multi-user and the
    # user-less startup sync paths; backfilled at the Postgres migration,
    # where the column also tightens to NOT NULL.
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
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
    filters, so `WHERE user_id = ?` is written exactly once. Legacy unowned
    rows (user_id NULL: pre-multi-user data, startup sync before its owner
    logs in) match no user by design — scripts/backfill_owner.py is the bridge.
    """
    return select(JournalEntry).where(JournalEntry.user_id == user_id)


def ensure_schema() -> None:
    """Add columns introduced after a DB was first created.

    SQLModel.metadata.create_all() creates missing tables but never ALTERs an
    existing one, so a column added to JournalEntry won't appear in a DB seeded
    before it. This idempotent SQLite migration backfills such columns. Call it
    once at startup, after create_all.
    """
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(journalentry)"))}
        # cols is empty if the table doesn't exist yet — create_all will include
        # the column, so there's nothing to migrate.
        if cols and "mood_reasoning" not in cols:
            conn.execute(text("ALTER TABLE journalentry ADD COLUMN mood_reasoning VARCHAR"))
            conn.commit()
        # Multi-user isolation column (ADR 0003). Note: a pre-existing DB keeps
        # its baked-in global UNIQUE(content_hash) — SQLite can't drop a column
        # constraint via ALTER. Harmless while the DB is single-user; the
        # constraint swaps to the composite one at the Postgres migration.
        if cols and "user_id" not in cols:
            conn.execute(text("ALTER TABLE journalentry ADD COLUMN user_id INTEGER"))
            conn.commit()
