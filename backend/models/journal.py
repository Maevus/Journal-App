from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine

from clock import utcnow_naive
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class JournalEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str  # "google_keep" | "goodnotes" | "text"
    content: str
    timestamp: datetime = Field(default_factory=utcnow_naive)  # when the entry was authored
    mood_score: Optional[float] = None  # 0.0 (very negative) – 1.0 (very positive)
    # One-sentence explanation of the mood score, from the same LLM call
    mood_reasoning: Optional[str] = None
    content_hash: str = Field(unique=True, index=True)
    # When the entry entered this system; NULL for rows predating the field
    imported_at: Optional[datetime] = Field(default_factory=utcnow_naive)
    # ID of the note at its source (Keep note id → keep.google.com/#NOTE/<id>)
    external_id: Optional[str] = None


def get_session() -> Generator:
    with Session(engine) as session:
        yield session


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
