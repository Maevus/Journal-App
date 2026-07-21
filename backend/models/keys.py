"""BYO API keys, encrypted at rest (ADR 0003, freemium F4).

One row per (user, provider) holds a user's own provider API key as Fernet
ciphertext — never the plaintext, never logged, never returned by the API. The
`provider` column (default "anthropic") means a future OpenAI/Gemini key is a
new row, not a schema change — the same provider-agnostic stance LLMUsage.model
takes for pricing.
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
    # Fernet token (ciphertext + auth tag). Bytes, so the raw key is never
    # coerced through a str representation on its way to storage.
    encrypted_key: bytes
    created_at: datetime = Field(default_factory=utcnow_naive)
