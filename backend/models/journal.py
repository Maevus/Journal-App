import os
from datetime import datetime
from typing import Generator, Optional

from sqlmodel import Field, Session, SQLModel, create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./journal.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class JournalEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str  # "google_keep" | "goodnotes" | "text"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    mood_score: Optional[float] = None  # 0.0 (very negative) – 1.0 (very positive)
    content_hash: str = Field(unique=True, index=True)


def get_session() -> Generator:
    with Session(engine) as session:
        yield session
