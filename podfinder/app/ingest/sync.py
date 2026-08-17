"""Sync enabled event sources into the local cache (the database).

The web app NEVER fetches store feeds during a request — pages and the
API read only from this cache. Each source is refreshed at most once per
``MAX_AGE_HOURS`` (daily by default), which keeps load on store websites
minimal no matter how much traffic the app itself gets.

Run manually or from cron:

    python -m app.ingest.sync            # refresh sources older than 24h
    python -m app.ingest.sync --force    # refresh everything now

The app also runs this automatically in the background (see
``app/main.py``), so cron is optional. Events are upserted by
(store, external_uid), so re-running is always safe.
"""

import argparse
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classify import classify
from app.database import SessionLocal, init_db
from app.ingest.base import RawEvent, SourceAdapter
from app.ingest.ics_source import IcsAdapter
from app.ingest.jsonld_source import JsonLdAdapter
from app.models import Event, EventSource

ADAPTERS: dict[str, SourceAdapter] = {
    "ics": IcsAdapter(),
    "jsonld": JsonLdAdapter(),
}

# How long a source's cached events stay fresh before it's re-fetched.
MAX_AGE_HOURS = 24


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


def is_fresh(source: EventSource, max_age_hours: float = MAX_AGE_HOURS) -> bool:
    """True if this source was synced recently enough to skip."""
    return (
        source.last_synced_at is not None
        and datetime.now() - source.last_synced_at < timedelta(hours=max_age_hours)
    )


def sync_source(session: Session, source: EventSource) -> bool:
    """Fetch one source into the cache. Returns True if it was refreshed."""
    adapter = ADAPTERS.get(source.kind)
    if adapter is None:
        print(f"  skipping {source.kind} source #{source.id}: no adapter")
        return False
    try:
        raw_events = adapter.fetch(source)
    except Exception as error:  # keep syncing other sources on failure
        source.last_error = str(error)[:400]
        print(f"  ERROR syncing source #{source.id} ({source.url}): {error}")
        return False
    count = upsert_events(session, source, raw_events)
    pruned = prune_stale_events(session, source, {r.external_uid for r in raw_events})
    source.last_error = None
    source.last_synced_at = datetime.now()
    print(f"  {source.store.name}: {count} events upserted, {pruned} pruned")
    return True


def sync_all(force: bool = False, max_age_hours: float = MAX_AGE_HOURS) -> int:
    """Refresh every enabled source whose cache is stale. Returns refresh count."""
    init_db()
    session = SessionLocal()
    refreshed = 0
    try:
        sources = session.scalars(
            select(EventSource).where(EventSource.enabled.is_(True))
        ).all()
        due = [s for s in sources if force or not is_fresh(s, max_age_hours)]
        skipped = len(sources) - len(due)
        if due or skipped:
            print(f"Syncing {len(due)} source(s), {skipped} still fresh (<{max_age_hours}h)...")
        for source in due:
            if sync_source(session, source):
                refreshed += 1
        # Clean up old events so the database doesn't grow forever.
        cutoff = datetime.now() - timedelta(days=7)
        old = session.scalars(select(Event).where(Event.start_time < cutoff)).all()
        for event in old:
            session.delete(event)
        session.commit()
        return refreshed
    finally:
        session.close()


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--force", action="store_true",
        help="refresh all sources even if their cache is still fresh",
    )
    arguments = argument_parser.parse_args()
    sync_all(force=arguments.force)
    print("Done.")
