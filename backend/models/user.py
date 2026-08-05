"""Identity models — the account and its login sessions (ADR 0003).

Everything a person owns foreign-keys to `User.id`. Identity is anchored on
Google's stable `sub` claim; `email` is a mutable display attribute, never a
key. `UserSession` is the server-side half of the httpOnly-cookie login —
only a token *hash* is stored (services/auth.py).
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from clock import utcnow_naive


class User(SQLModel, table=True):
    # "users" not SQLModel's default "user": unquoted `user` is a reserved word
    # in Postgres (Decision 4), a latent footgun. Also matches the plan/ADR naming.
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)  # our own PK
    google_sub: str = Field(unique=True, index=True)  # Google's stable identity anchor
    email: str  # display attribute — deliberately not unique
    created_at: datetime = Field(default_factory=utcnow_naive)


class UserSession(SQLModel, table=True):
    # "sessions" not default "usersession"; class is UserSession to avoid colliding with sqlmodel.Session.
    __tablename__ = "sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    # sha256 of the raw cookie token — we store the hash, never the raw secret.
    token_hash: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    expires_at: datetime  # past → the session no longer resolves
