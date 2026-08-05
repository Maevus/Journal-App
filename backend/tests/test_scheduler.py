"""Tests for the GoodNotes scan scheduler wiring in main.py.

`_start_goodnotes_scheduler` is tested directly, not through the full lifespan,
so job registration can be asserted without standing up the whole app."""
import config
import main


def test_scheduler_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(config, "GOODNOTES_SCAN_INTERVAL_MIN", 0)
    scheduler = main._start_goodnotes_scheduler(scan=lambda: None)
    assert scheduler is None


async def test_scheduler_registers_job_when_interval_positive(monkeypatch):
    # async so AsyncIOScheduler.start() finds a running event loop.
    monkeypatch.setattr(config, "GOODNOTES_SCAN_INTERVAL_MIN", 15)

    async def fake_scan():
        return None

    scheduler = main._start_goodnotes_scheduler(scan=fake_scan)
    try:
        assert scheduler is not None
        job = scheduler.get_job("goodnotes_scan")
        assert job is not None
        # Interval trigger reflects the configured minutes.
        assert job.trigger.interval.total_seconds() == 15 * 60
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
