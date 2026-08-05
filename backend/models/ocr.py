"""Tombstones for pages that OCR'd to nothing.

A blank page produces no JournalEntry, so without this row the scan re-OCRs it
every time.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class BlankPage(SQLModel, table=True):
    __tablename__ = "blank_pages"

    # Keyed (source, external_id), matching _page_already_imported.
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_blank_page"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str
    external_id: str = Field(index=True)
    # Whose sync saw it. NULL when the connector's owner isn't resolvable yet.
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    seen_at: datetime = Field(default_factory=utcnow_naive)
