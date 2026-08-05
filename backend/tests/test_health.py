"""The container healthcheck endpoint.

It replaced /trends, which gained auth and then failed the probe on every
private deployment while the app was fine. These tests pin the two properties
that made it drift-prone: no auth, and a real database round-trip.
"""
from sqlalchemy.exc import OperationalError

import main


def test_health_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_is_503_when_the_database_is_unreachable(client):
    """Green must mean "process AND database", not "process".

    A port check already proves the process is up; the probe only earns its
    keep if it goes red when the DB is gone.
    """
    def dead_session():
        class _Dead:
            def exec(self, *a, **k):
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        yield _Dead()

    main.app.dependency_overrides[main.get_session] = dead_session
    try:
        resp = client.get("/health")
    finally:
        main.app.dependency_overrides.pop(main.get_session)

    assert resp.status_code == 503


def test_health_carries_no_rate_limiter():
    """Throttling the healthcheck restarts the container under exactly the load
    it is supposed to survive."""
    route = next(r for r in main.app.routes if getattr(r, "path", None) == "/health")
    assert route.dependencies == []
