from datetime import datetime
from typing import Generator, Optional

from sqlmodel import Field, Session, SQLModel, create_engine

from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class JournalEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str  # "google_keep" | "goodnotes" | "text"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)  # when the entry was authored
    mood_score: Optional[float] = None  # 0.0 (very negative) – 1.0 (very positive)
    content_hash: str = Field(unique=True, index=True)
    # When the entry entered this system; NULL for rows predating the field
    imported_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    # ID of the note at its source (Keep note id → keep.google.com/#NOTE/<id>)
    external_id: Optional[str] = None


def get_session() -> Generator:
    with Session(engine) as session:
        yield session
