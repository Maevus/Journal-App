"""BYO API keys, encrypted at rest (ADR 0003, freemium F4).

One row per (user, provider): Fernet ciphertext only, never plaintext,
logged, or returned. `provider` (default "anthropic") means a new provider
is a new row, not a schema change — same stance as LLMUsage.model.
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class UserApiKey(SQLModel, table=True):
    __tablename__ = "user_api_keys"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    provider: str = Field(default="anthropic", index=True)
    # Fernet token (ciphertext + auth tag); bytes so the raw key never passes through a str.
    encrypted_key: bytes
    created_at: datetime = Field(default_factory=utcnow_naive)
