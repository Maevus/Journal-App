"""Store and retrieve users' own API keys, encrypted at rest (ADR 0003, F4).

A deep module: callers pass/receive plaintext keys; Fernet encryption is
entirely internal, so no caller ever handles ciphertext. The plaintext is
never logged and never leaves this boundary except back to the LLM client.
"""
from typing import Optional

from cryptography.fernet import Fernet
from sqlmodel import Session, select

import config
from models.keys import UserApiKey


def _fernet() -> Fernet:
    """Build the cipher from the operator's APP_ENCRYPTION_KEY.

    Raised lazily, not at import — free-tier users with no key configured are
    unaffected; only storing/reading a BYO key needs this, so a missing key
    surfaces here as a clear error, not a crash elsewhere.
    """
    if not config.APP_ENCRYPTION_KEY:
        raise RuntimeError(
            "APP_ENCRYPTION_KEY is not set — required to store or read BYO API keys"
        )
    return Fernet(config.APP_ENCRYPTION_KEY)


def store_user_key(db: Session, *, user_id: int, raw_key: str, provider: str = "anthropic") -> None:
    """Encrypt and persist `user_id`'s key for `provider`, replacing any prior
    one (a user re-entering their key overwrites, never accumulates rows)."""
    encrypted = _fernet().encrypt(raw_key.encode())
    row = db.exec(
        select(UserApiKey)
        .where(UserApiKey.user_id == user_id)
        .where(UserApiKey.provider == provider)
    ).first()
    if row is None:
        row = UserApiKey(user_id=user_id, provider=provider, encrypted_key=encrypted)
    else:
        row.encrypted_key = encrypted
    db.add(row)
    db.commit()


def has_user_key(db: Session, *, user_id: int, provider: str = "anthropic") -> bool:
    """Whether `user_id` has a stored key — a row-existence check that does NOT
    decrypt, so callers (the quota gate) can ask this without APP_ENCRYPTION_KEY set."""
    row = db.exec(
        select(UserApiKey.id)
        .where(UserApiKey.user_id == user_id)
        .where(UserApiKey.provider == provider)
    ).first()
    return row is not None


def remove_user_key(db: Session, *, user_id: int, provider: str = "anthropic") -> None:
    """Delete `user_id`'s stored key for `provider`. Idempotent — removing a key
    that isn't there is a no-op. Reverts the user to the app key / free tier."""
    row = db.exec(
        select(UserApiKey)
        .where(UserApiKey.user_id == user_id)
        .where(UserApiKey.provider == provider)
    ).first()
    if row is not None:
        db.delete(row)
        db.commit()


def get_user_key(db: Session, *, user_id: int, provider: str = "anthropic") -> Optional[str]:
    """The plaintext key for `user_id`/`provider`, or None if they haven't set one."""
    row = db.exec(
        select(UserApiKey)
        .where(UserApiKey.user_id == user_id)
        .where(UserApiKey.provider == provider)
    ).first()
    if row is None:
        return None
    return _fernet().decrypt(row.encrypted_key).decode()
