import asyncio
from datetime import datetime

import pytest

from bot.services import analytics


@pytest.fixture(autouse=True)
def reset_analytics():
    analytics.configure(None)
    analytics._sessions.clear()
    yield
    analytics.configure(None)
    analytics._sessions.clear()


class FakeSession:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.store.append(obj)

    async def commit(self):
        pass


def test_event_is_stored_with_props_and_session():
    stored = []
    analytics.configure(lambda: FakeSession(stored))

    async def run():
        analytics.track(5, "payment_started", provider="yookassa", months=3, amount=499.0)
        await analytics.flush()

    asyncio.run(run())
    assert len(stored) == 1
    event = stored[0]
    assert (event.user_id, event.name) == (5, "payment_started")
    assert event.props == {"provider": "yookassa", "months": 3, "amount": 499.0}
    assert len(event.session_id) == 32


def test_track_never_raises_when_storage_fails():
    def broken_factory():
        raise RuntimeError("database is down")

    analytics.configure(broken_factory)

    async def run():
        analytics.track(1, "start", source="organic")
        await analytics.flush()

    asyncio.run(run())


def test_track_never_raises_on_failing_commit():
    class FailingCommit(FakeSession):
        async def commit(self):
            raise RuntimeError("constraint violated")

    analytics.configure(lambda: FailingCommit([]))

    async def run():
        analytics.track(1, "start", source="organic")
        await analytics.flush()

    asyncio.run(run())


def test_track_without_running_loop_or_configuration_is_a_no_op():
    analytics.track(1, "start", source="organic")
    analytics.configure(lambda: FakeSession([]))
    analytics.track(1, "start", source="organic")


def test_unknown_event_name_and_odd_props_are_handled():
    stored = []
    analytics.configure(lambda: FakeSession(stored))

    async def run():
        analytics.track(1, "made_up_event")
        analytics.track(1, "payment_succeeded", provider="stars", at=datetime(2026, 1, 1), raw=object())
        await analytics.flush()

    asyncio.run(run())
    assert [e.name for e in stored] == ["payment_succeeded"]
    assert stored[0].props["at"] == "2026-01-01 00:00:00"


def test_session_rotates_after_thirty_idle_minutes():
    first = analytics.session_id_for(9, now=0)
    assert analytics.session_id_for(9, now=29 * 60) == first
    # Activity extends the session: the gap is measured from the last event.
    assert analytics.session_id_for(9, now=58 * 60) == first
    assert analytics.session_id_for(9, now=58 * 60 + 30 * 60) != first
    assert analytics.session_id_for(10, now=0) != first
