"""Account deletion — the user row and every row that belongs to it
(security checklist §2, §10).

Deletion is ordered application-level statements, not one DELETE on `users`:
only entry_embeddings→journalentry declares ON DELETE CASCADE, so every other
child has to be named here.
"""
import logging

from sqlalchemy import delete
from sqlmodel import Session

from models.journal import JournalEntry
from models.keys import UserApiKey
from models.ocr import BlankPage
from models.rate_limit import RateLimitCounter
from models.usage import LLMUsage, QuotaReservation
from models.user import User, UserSession
from services.auth import is_demo_user
from services.embeddings import delete_user_embeddings

logger = logging.getLogger(__name__)


def _delete_where(db: Session, statement) -> int:
    """Run a bulk DELETE and return the row count. Uncommitted."""
    return db.execute(statement.execution_options(synchronize_session=False)).rowcount or 0


def delete_account(db: Session, user: User) -> dict[str, int]:
    """Erase `user` and everything they own. Returns {table: rows deleted}.

    Children first, `users` last, one commit at the end — a failure leaves the
    account whole. `user` is unusable afterwards; read what you need first.
    """
    if is_demo_user(user):
        raise ValueError("The shared demo account cannot be deleted")

    user_id = user.id
    counts = {
        # Outside SQLModel.metadata, so no ORM cascade reaches it.
        "entry_embeddings": delete_user_embeddings(db, user_id=user_id),
        "journalentry": _delete_where(
            db, delete(JournalEntry).where(JournalEntry.user_id == user_id)
        ),
        "user_api_keys": _delete_where(
            db, delete(UserApiKey).where(UserApiKey.user_id == user_id)
        ),
        "llmusage": _delete_where(db, delete(LLMUsage).where(LLMUsage.user_id == user_id)),
        "quotareservation": _delete_where(
            db, delete(QuotaReservation).where(QuotaReservation.user_id == user_id)
        ),
        "blank_pages": _delete_where(db, delete(BlankPage).where(BlankPage.user_id == user_id)),
        # Buckets are "{identity}|{tier}|{window}" — services/rate_limit.py.
        "rate_limit_counters": _delete_where(
            db,
            delete(RateLimitCounter).where(
                RateLimitCounter.bucket.startswith(f"user:{user_id}|")
            ),
        ),
        # Every session, not just the caller's — deletion logs out every device.
        "sessions": _delete_where(db, delete(UserSession).where(UserSession.user_id == user_id)),
        "users": _delete_where(db, delete(User).where(User.id == user_id)),
    }
    db.commit()
    logger.info("deleted account %s: %s", user_id, counts)
    return counts
