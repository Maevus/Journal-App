"""Fixed-window rate-limit counters, in the database on purpose.

A module-level dict would be simpler and quietly wrong. The Dockerfile and the
production manifest both anticipate scaling out, and N uvicorn workers each
holding their own counters multiply every limit by N — with no symptom until
someone leans on it. The counters live where the workers already agree about
everything else.

One row per (bucket, window). The row IS the read: a single
`INSERT … ON CONFLICT DO UPDATE … RETURNING hits` both increments the counter
and reports the new value, so there is no gap between deciding and recording.
That is the difference from services/quota.py, which reads the meter, closes the
session, then compares — a genuine read-then-act race that needed a lock bolted
on to close. This table needs no lock because the decision and the write are the
same statement. Postgres has supported that shape since 9.5, SQLite since 3.24
(and `RETURNING` since 3.35), which covers the test and standalone-demo path.
"""
from datetime import datetime

from sqlmodel import Field, SQLModel


class RateLimitCounter(SQLModel, table=True):
    __tablename__ = "rate_limit_counters"

    # Composite primary key, no surrogate id. (bucket, window_start) is the
    # natural key and is exactly what ON CONFLICT targets, so a surrogate would
    # add a sequence for every increment to contend on and buy nothing. The
    # bucket string is "{identity}|{tier}|{window}" — see services/rate_limit.py,
    # which is the only thing allowed to build one.
    bucket: str = Field(primary_key=True)
    window_start: datetime = Field(primary_key=True)

    hits: int = Field(default=0)

    # When this row stops meaning anything, so the sweep is one indexed range
    # delete that needs no knowledge of which window the row belongs to.
    # Carried on the row rather than derived because the two windows have
    # different lengths and a third would otherwise need code changes here.
    expires_at: datetime = Field(index=True)
