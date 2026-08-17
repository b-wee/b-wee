"""Base types for event-source adapters.

An adapter takes an EventSource (e.g. a store's ICS calendar URL) and
returns a list of RawEvents. The sync step classifies and upserts them.
To support a new calendar platform, write a new adapter and register it
in ``app/ingest/sync.py``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from app.models import EventSource


@dataclass
class RawEvent:
    """An event as it comes out of an upstream feed, before classification."""

    external_uid: str
    title: str
    start_time: datetime
    end_time: Optional[datetime] = None
    description: Optional[str] = None
    url: Optional[str] = None


class SourceAdapter(Protocol):
    def fetch(self, source: EventSource) -> list[RawEvent]:
        """Fetch and parse upstream events. Raise on failure."""
        ...
