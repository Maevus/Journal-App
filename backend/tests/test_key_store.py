"""Slice F4a: BYO API keys, encrypted at rest.

The store hands back plaintext; encryption (Fernet, keyed on
APP_ENCRYPTION_KEY) is internal. Guarantee: the bytes in the DB are
ciphertext, never the raw key.
"""
import pytest
from cryptography.fernet import Fernet
from sqlmodel import select

import config
import services.key_store as key_store
from models.keys import UserApiKey


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """A per-test Fernet key so the store can encrypt/decrypt without depending
    on the operator's real APP_ENCRYPTION_KEY."""
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_stored_key_round_trips_and_is_encrypted_at_rest(session):
    secret = "sk-ant-super-secret-value"
    key_store.store_user_key(session, user_id=1, raw_key=secret)

    # The interface returns the plaintext...
    assert key_store.get_user_key(session, user_id=1) == secret

    # ...but the persisted column is ciphertext — the raw key never touches disk.
    row = session.exec(select(UserApiKey).where(UserApiKey.user_id == 1)).one()
    assert secret not in str(row.encrypted_key)
    assert secret.encode() not in row.encrypted_key


def test_get_user_key_is_none_when_unset(session):
    """A free-tier user has no stored key — the client falls back to the app key."""
    assert key_store.get_user_key(session, user_id=99) is None


def test_remove_user_key_deletes_it(session):
    key_store.store_user_key(session, user_id=1, raw_key="sk-ant-x")
    key_store.remove_user_key(session, user_id=1)

    assert key_store.get_user_key(session, user_id=1) is None
    assert key_store.has_user_key(session, user_id=1) is False


def test_remove_user_key_is_idempotent(session):
    """Removing a key that isn't there is a no-op, not an error."""
    key_store.remove_user_key(session, user_id=404)  # must not raise


def test_store_user_key_overwrites_in_place(session):
    """Re-entering a key replaces the old one; it never accumulates rows."""
    key_store.store_user_key(session, user_id=1, raw_key="sk-ant-first")
    key_store.store_user_key(session, user_id=1, raw_key="sk-ant-second")

    assert key_store.get_user_key(session, user_id=1) == "sk-ant-second"
    rows = session.exec(select(UserApiKey).where(UserApiKey.user_id == 1)).all()
    assert len(rows) == 1
