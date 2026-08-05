"""Fixed-window rate-limit counters, in the database on purpose.

A module-level dict would multiply every limit by N once there's more than
one uvicorn worker, with no symptom until someone leans on it.

One row per (bucket, window). The row IS the read: a single
`INSERT … ON CONFLICT DO UPDATE … RETURNING hits` both increments and
reports the new value, so no lock is needed (unlike services/quota.py's
read-then-act). Needs Postgres 9.5+ or SQLite 3.35+ (RETURNING support).
"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class RateLimitCounter(SQLModel, table=True):
    __tablename__ = "rate_limit_counters"

    # Composite primary key (no surrogate id): (bucket, window_start) is
    # exactly what ON CONFLICT targets — dropping it turns every limit into
    # no limit. Bucket string is "{identity}|{tier}|{window}", built only by
    # services/rate_limit.py.
    bucket: str = Field(primary_key=True)
    window_start: datetime = Field(primary_key=True)

    hits: int = Field(default=0)

    # When this row stops mattering, so the sweep is a plain indexed range
    # delete with no per-window knowledge.
    expires_at: datetime = Field(index=True)
