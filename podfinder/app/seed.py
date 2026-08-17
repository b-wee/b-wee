"""Seed the database with sample stores and events.

    python -m app.seed

Loads data/stores.sample.json and generates the next four weeks of each
store's weekly events, so the app has something to show right away.
Real deployments replace this with actual stores + ICS sources and run
``python -m app.ingest.sync`` instead.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.classify import classify
from app.database import SessionLocal, init_db
from app.models import Event, EventSource, Store

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "stores.sample.json"
WEEKS_TO_GENERATE = 4


def next_occurrences(weekday: int, hour: int, weeks: int) -> list[datetime]:
    """The next ``weeks`` occurrences of a weekly slot (0=Monday)."""
    now = datetime.now()
    days_ahead = (weekday - now.weekday()) % 7
    first = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if first < now:
        first += timedelta(days=7)
    return [first + timedelta(weeks=i) for i in range(weeks)]


def seed() -> None:
    init_db()
    data = json.loads(DATA_FILE.read_text())
    session = SessionLocal()
    try:
        for entry in data["stores"]:
            store = session.scalar(select(Store).where(Store.slug == entry["slug"]))
            if store is None:
                store = Store(slug=entry["slug"])
                session.add(store)
            store.name = entry["name"]
            store.address = entry.get("address")
            store.city = entry["city"]
            store.state = entry["state"]
            store.website = entry.get("website")
            session.flush()

            for source_entry in entry.get("sources", []):
                exists = session.scalar(
                    select(EventSource).where(
                        EventSource.store_id == store.id,
                        EventSource.url == source_entry["url"],
                    )
                )
                if exists is None:
                    session.add(
                        EventSource(
                            store_id=store.id,
                            kind=source_entry.get("kind", "ics"),
                            url=source_entry["url"],
                        )
                    )

            for weekly in entry.get("weekly_events", []):
                for start in next_occurrences(
                    weekly["weekday"], weekly["hour"], WEEKS_TO_GENERATE
                ):
                    uid = f"seed:{weekly['title']}:{start.isoformat()}"
                    if session.scalar(
                        select(Event).where(
                            Event.store_id == store.id, Event.external_uid == uid
                        )
                    ):
                        continue
                    result = classify(weekly["title"])
                    session.add(
                        Event(
                            store_id=store.id,
                            external_uid=uid,
                            title=weekly["title"],
                            game=result.game,
                            format=result.format,
                            is_casual=result.is_casual,
                            start_time=start,
                            end_time=start + timedelta(hours=3),
                            entry_fee=weekly.get("fee"),
                        )
                    )
        session.commit()
        print("Seeded sample stores and events.")
    finally:
        session.close()


if __name__ == "__main__":
    seed()
