from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Event, Store
from app.seed import seed


def test_seed_is_idempotent():
    Base.metadata.drop_all(engine)
    seed()
    session = SessionLocal()
    store_count = len(session.scalars(select(Store)).all())
    event_count = len(session.scalars(select(Event)).all())
    assert store_count == 5
    assert event_count > 0
    session.close()

    # Running again must not duplicate anything.
    seed()
    session = SessionLocal()
    assert len(session.scalars(select(Store)).all()) == store_count
    assert len(session.scalars(select(Event)).all()) == event_count
    session.close()


def test_seeded_events_are_classified_with_coordinates():
    Base.metadata.drop_all(engine)
    seed()
    session = SessionLocal()
    stores = session.scalars(select(Store)).all()
    assert all(store.latitude is not None for store in stores)
    commander = session.scalars(
        select(Event).where(Event.format == "commander")
    ).all()
    assert commander
    assert all(event.game == "magic" for event in commander)
    session.close()
