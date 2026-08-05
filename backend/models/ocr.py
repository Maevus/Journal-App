"""Tombstones for pages that OCR'd to nothing.

A page with no text produces no JournalEntry, so `_page_already_imported` never
sees it and the page is re-transcribed on every scan — a paid vision call per
blank page, forever. The tombstone is the row that records "seen, empty".
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class BlankPage(SQLModel, table=True):
    __tablename__ = "blank_pages"

    # Keyed like the skip it replaces (source, external_id), not per-user: both
    # share the single-account connector assumption in _resolve_sync_owner.
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_blank_page"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source: str
    external_id: str = Field(index=True)
    # Whose sync saw it. NULL when the connector's owner isn't resolvable yet.
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    seen_at: datetime = Field(default_factory=utcnow_naive)
