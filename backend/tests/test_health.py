"""The container healthcheck endpoint: no auth, real database round-trip, no
rate limiter."""
from sqlalchemy.exc import OperationalError

import main


def test_health_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_is_503_when_the_database_is_unreachable(client):
    """Green must mean process AND database, not just process."""
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
    """A throttled healthcheck restarts the container under load."""
    route = next(r for r in main.app.routes if getattr(r, "path", None) == "/health")
    assert route.dependencies == []
