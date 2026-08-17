"""Tests for the cache/sync engine: upserts, pruning, and daily staleness."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine, init_db
from app.ingest import sync
from app.ingest.base import RawEvent
from app.models import Event, EventSource, Store


class FakeAdapter:
    """Adapter double that serves a scripted list and counts fetches."""

    def __init__(self, raw_events):
        self.raw_events = raw_events
        self.fetch_count = 0

    def fetch(self, source):
        self.fetch_count += 1
        return self.raw_events


def make_raw(title: str, days_ahead: int) -> RawEvent:
    start = datetime.now() + timedelta(days=days_ahead)
    return RawEvent(
        external_uid=f"uid-{title}",
        title=title,
        start_time=start,
        end_time=start + timedelta(hours=3),
    )


@pytest.fixture()
def source(monkeypatch):
    Base.metadata.drop_all(engine)
    init_db()
    session = SessionLocal()
    store = Store(slug="sync-store", name="Sync Store", city="Springfield", state="IL")
    session.add(store)
    session.flush()
    event_source = EventSource(store_id=store.id, kind="fake", url="https://example.com/feed")
    session.add(event_source)
    session.commit()
    yield event_source
    session.close()


def test_sync_upserts_and_classifies(source, monkeypatch):
    adapter = FakeAdapter([make_raw("Commander Night", 2)])
    monkeypatch.setitem(sync.ADAPTERS, "fake", adapter)
    assert sync.sync_all() == 1

    session = SessionLocal()
    events = session.scalars(select(Event)).all()
    assert len(events) == 1
    assert events[0].game == "magic"
    assert events[0].format == "commander"
    session.close()


def test_resync_updates_in_place_and_prunes(source, monkeypatch):
    adapter = FakeAdapter([make_raw("Commander Night", 2), make_raw("Draft", 3)])
    monkeypatch.setitem(sync.ADAPTERS, "fake", adapter)
    sync.sync_all()

    # Second sync: Draft disappeared upstream, Commander Night still there.
    adapter.raw_events = [make_raw("Commander Night", 2)]
    assert sync.sync_all(force=True) == 1

    session = SessionLocal()
    titles = [event.title for event in session.scalars(select(Event)).all()]
    assert titles == ["Commander Night"]  # no duplicate, Draft pruned
    session.close()


def test_fresh_source_is_skipped_within_a_day(source, monkeypatch):
    adapter = FakeAdapter([make_raw("Commander Night", 2)])
    monkeypatch.setitem(sync.ADAPTERS, "fake", adapter)

    assert sync.sync_all() == 1
    # Synced moments ago -> the daily cache is fresh, feed must NOT be hit.
    assert sync.sync_all() == 0
    assert adapter.fetch_count == 1


def test_force_overrides_freshness(source, monkeypatch):
    adapter = FakeAdapter([make_raw("Commander Night", 2)])
    monkeypatch.setitem(sync.ADAPTERS, "fake", adapter)

    sync.sync_all()
    assert sync.sync_all(force=True) == 1
    assert adapter.fetch_count == 2


def test_stale_source_is_resynced(source, monkeypatch):
    adapter = FakeAdapter([make_raw("Commander Night", 2)])
    monkeypatch.setitem(sync.ADAPTERS, "fake", adapter)

    sync.sync_all()
    # Pretend the last sync happened 25 hours ago.
    session = SessionLocal()
    stored = session.get(EventSource, source.id)
    stored.last_synced_at = datetime.now() - timedelta(hours=25)
    session.commit()
    session.close()

    assert sync.sync_all() == 1
    assert adapter.fetch_count == 2


def test_fetch_error_recorded_and_survivable(source, monkeypatch):
    class ExplodingAdapter:
        def fetch(self, source):
            raise RuntimeError("feed unreachable")

    monkeypatch.setitem(sync.ADAPTERS, "fake", ExplodingAdapter())
    assert sync.sync_all() == 0

    session = SessionLocal()
    stored = session.get(EventSource, source.id)
    assert "feed unreachable" in (stored.last_error or "")
    session.close()
