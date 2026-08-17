"""SQLAlchemy models: stores, their event feeds, and the events themselves."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Store(Base):
    """A local game store whose events we aggregate."""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[Optional[str]] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(2))
    website: Mapped[Optional[str]] = mapped_column(String(200))
    latitude: Mapped[Optional[float]]
    longitude: Mapped[Optional[float]]

    sources: Mapped[list["EventSource"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class EventSource(Base):
    """Where a store's events come from (e.g. a public ICS calendar URL).

    Most stores publish their event calendar through Google Calendar,
    Squarespace, or Wix — all of which expose an ICS feed we can poll.
    """

    __tablename__ = "event_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="ics")  # ics | manual
    url: Mapped[Optional[str]] = mapped_column(String(400))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(String(400))

    store: Mapped[Store] = relationship(back_populates="sources")
    events: Mapped[list["Event"]] = relationship(back_populates="source")


class Event(Base):
    """A single scheduled event at a store.

    ``external_uid`` is the stable id from the upstream feed (the ICS UID),
    so re-syncing updates events in place instead of duplicating them.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("store_id", "external_uid", name="uq_store_external_uid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event_sources.id"))
    external_uid: Mapped[str] = mapped_column(String(200))

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(String(2000))
    game: Mapped[str] = mapped_column(String(30), index=True)      # e.g. "magic"
    format: Mapped[Optional[str]] = mapped_column(String(30), index=True)  # e.g. "commander"
    is_casual: Mapped[bool] = mapped_column(default=True, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    entry_fee: Mapped[Optional[str]] = mapped_column(String(40))
    url: Mapped[Optional[str]] = mapped_column(String(400))

    store: Mapped[Store] = relationship(back_populates="events")
    source: Mapped[Optional[EventSource]] = relationship(back_populates="events")
