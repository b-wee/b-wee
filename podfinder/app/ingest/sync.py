"""Sync all enabled event sources into the database.

Run manually or from cron:

    python -m app.ingest.sync

Events are upserted by (store, external_uid), so re-running is safe.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify import classify
from app.database import SessionLocal, init_db
from app.ingest.base import RawEvent, SourceAdapter
from app.ingest.ics_source import IcsAdapter
from app.models import Event, EventSource

ADAPTERS: dict[str, SourceAdapter] = {
    "ics": IcsAdapter(),
}


def upsert_events(session: Session, source: EventSource, raw_events: list[RawEvent]) -> int:
    """Insert or update the given raw events for a source's store."""
    count = 0
    for raw in raw_events:
        result = classify(raw.title, raw.description or "")
        existing = session.scalar(
            select(Event).where(
                Event.store_id == source.store_id,
                Event.external_uid == raw.external_uid,
            )
        )
        event = existing or Event(
            store_id=source.store_id, external_uid=raw.external_uid
        )
        event.source_id = source.id
        event.title = raw.title
        event.description = raw.description
        event.start_time = raw.start_time
        event.end_time = raw.end_time
        event.url = raw.url
        event.game = result.game
        event.format = result.format
        event.is_casual = result.is_casual
        if existing is None:
            session.add(event)
        count += 1
    return count


def prune_stale_events(session: Session, source: EventSource, keep_uids: set[str]) -> int:
    """Remove future events that disappeared from the upstream feed."""
    stale = session.scalars(
        select(Event).where(
            Event.source_id == source.id,
            Event.start_time >= datetime.now(),
            Event.external_uid.notin_(keep_uids) if keep_uids else True,
        )
    ).all()
    for event in stale:
        session.delete(event)
    return len(stale)


def sync_source(session: Session, source: EventSource) -> None:
    adapter = ADAPTERS.get(source.kind)
    if adapter is None:
        print(f"  skipping {source.kind} source #{source.id}: no adapter")
        return
    try:
        raw_events = adapter.fetch(source)
    except Exception as error:  # keep syncing other sources on failure
        source.last_error = str(error)[:400]
        print(f"  ERROR syncing source #{source.id} ({source.url}): {error}")
        return
    count = upsert_events(session, source, raw_events)
    pruned = prune_stale_events(session, source, {r.external_uid for r in raw_events})
    source.last_error = None
    source.last_synced_at = datetime.now()
    print(f"  {source.store.name}: {count} events upserted, {pruned} pruned")


def sync_all() -> None:
    init_db()
    session = SessionLocal()
    try:
        sources = session.scalars(
            select(EventSource).where(EventSource.enabled.is_(True))
        ).all()
        print(f"Syncing {len(sources)} source(s)...")
        for source in sources:
            sync_source(session, source)
        # Clean up old events so the database doesn't grow forever.
        cutoff = datetime.now() - timedelta(days=7)
        old = session.scalars(select(Event).where(Event.start_time < cutoff)).all()
        for event in old:
            session.delete(event)
        session.commit()
        print("Done.")
    finally:
        session.close()


if __name__ == "__main__":
    sync_all()
