"""Account deletion against real Postgres.

Gated on TEST_DATABASE_URL. SQLite enforces no foreign keys and has no
entry_embeddings table, so a child table missing from the wipe surfaces only
here — as an IntegrityError on the `users` delete, or a surviving vector.
"""
import os
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from alembic import command
from alembic.config import Config
from conftest import require_test_database_url
from models.journal import JournalEntry
from models.keys import UserApiKey
from models.ocr import BlankPage
from models.rate_limit import RateLimitCounter
from models.usage import LLMUsage, QuotaReservation
from models.user import User, UserSession
from services.account import delete_account

_BACKEND = Path(__file__).resolve().parent.parent

# A literal unit vector, so the test never loads the ONNX embedding model.
_VECTOR = "[" + ",".join(["0.1"] * 384) + "]"

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to a scratch pgvector Postgres DB (name must end in "
    "`_test`) to run the account-deletion Postgres tests — they drop every table on the target",
)


def _alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    # % is ConfigParser interpolation syntax — escape it so a URL-encoded
    # password doesn't raise. get_main_option un-escapes on read.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def pg():
    """Fresh schema at head on the test DB. Cleans it down afterward."""
    url = require_test_database_url()
    cfg = _alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()
    command.downgrade(cfg, "base")


def _seed_user(engine, tag: str) -> int:
    """A user owning one row in every table that keys on them. Returns the id."""
    with Session(engine) as s:
        user = User(google_sub=f"sub-{tag}", email=f"{tag}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        uid = user.id

        entry = JournalEntry(
            user_id=uid, source="text", content=f"{tag} entry", content_hash=f"hash-{tag}"
        )
        s.add(entry)
        s.commit()
        s.refresh(entry)

        s.add_all([
            UserSession(
                token_hash=f"token-{tag}",
                user_id=uid,
                expires_at=datetime(2030, 1, 1),
            ),
            UserApiKey(user_id=uid, provider="anthropic", encrypted_key=b"ciphertext"),
            LLMUsage(user_id=uid, model="claude", input_tokens=10, output_tokens=20, cost=0.5),
            QuotaReservation(user_id=uid, amount=0.25),
            BlankPage(source="goodnotes", external_id=f"page-{tag}", user_id=uid),
            RateLimitCounter(
                bucket=f"user:{uid}|chat|min",
                window_start=datetime(2026, 8, 4, 10, 30),
                hits=3,
                expires_at=datetime(2026, 8, 4, 11, 0),
            ),
        ])
        s.execute(
            text(
                "INSERT INTO entry_embeddings (entry_id, user_id, embedding) "
                "VALUES (:eid, :uid, (:emb)::vector)"
            ),
            {"eid": entry.id, "uid": uid, "emb": _VECTOR},
        )
        s.commit()
    return uid


def _row_counts(engine, user_id: int) -> dict[str, int]:
    with Session(engine) as s:
        def n(statement):
            return len(s.exec(statement).all())

        return {
            "journalentry": n(select(JournalEntry).where(JournalEntry.user_id == user_id)),
            "user_api_keys": n(select(UserApiKey).where(UserApiKey.user_id == user_id)),
            "llmusage": n(select(LLMUsage).where(LLMUsage.user_id == user_id)),
            "quotareservation": n(
                select(QuotaReservation).where(QuotaReservation.user_id == user_id)
            ),
            "blank_pages": n(select(BlankPage).where(BlankPage.user_id == user_id)),
            "rate_limit_counters": n(
                select(RateLimitCounter).where(
                    RateLimitCounter.bucket.startswith(f"user:{user_id}|")
                )
            ),
            "sessions": n(select(UserSession).where(UserSession.user_id == user_id)),
            "users": n(select(User).where(User.id == user_id)),
            "entry_embeddings": s.execute(
                text("SELECT count(*) FROM entry_embeddings WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar_one(),
        }


def test_delete_account_clears_every_table_including_the_vector_store(pg):
    """Postgres enforces the FKs, so this also pins the delete order."""
    uid = _seed_user(pg, "alice")
    assert all(c > 0 for c in _row_counts(pg, uid).values())  # seeding worked

    with Session(pg) as s:
        delete_account(s, s.get(User, uid))

    remaining = _row_counts(pg, uid)
    assert remaining == dict.fromkeys(remaining, 0)


def test_delete_account_leaves_the_other_user_whole(pg):
    alice = _seed_user(pg, "alice")
    bob = _seed_user(pg, "bob")
    bob_before = _row_counts(pg, bob)

    with Session(pg) as s:
        delete_account(s, s.get(User, alice))

    assert _row_counts(pg, bob) == bob_before
