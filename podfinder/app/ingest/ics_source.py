"""Adapter for ICS/iCal feeds (Google Calendar, Squarespace, Wix, etc.).

Handles both one-off VEVENTs and simple recurring rules (RRULE) by
expanding recurrences over a bounded window, since store calendars are
usually "every Wednesday at 6pm" style entries.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import httpx
from dateutil.rrule import rrulestr
from icalendar import Calendar

from app.ingest.base import RawEvent
from app.models import EventSource

# How far ahead to expand recurring events.
LOOKAHEAD_DAYS = 45


def _to_naive_local(value) -> Optional[datetime]:
    """Normalize ICS date/datetime values to naive local datetimes.

    We store naive datetimes and treat them as the store's local time —
    good enough for "what's happening near me this week".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
    if isinstance(value, date):
        # All-day event: treat as starting at noon so it sorts sensibly.
        return datetime.combine(value, time(12, 0))
    return None


def parse_ics(text: str, now: Optional[datetime] = None) -> list[RawEvent]:
    """Parse ICS text into RawEvents, expanding recurrences."""
    now = now or datetime.now()
    window_end = now + timedelta(days=LOOKAHEAD_DAYS)
    calendar = Calendar.from_ical(text)
    events: list[RawEvent] = []

    for component in calendar.walk("VEVENT"):
        uid = str(component.get("UID", "")) or None
        title = str(component.get("SUMMARY", "")).strip()
        if not title:
            continue
        description = str(component.get("DESCRIPTION", "")).strip() or None
        url = str(component.get("URL", "")).strip() or None

        dtstart = component.get("DTSTART")
        start = _to_naive_local(dtstart.dt if dtstart else None)
        if start is None:
            continue
        dtend = component.get("DTEND")
        end = _to_naive_local(dtend.dt if dtend else None)
        duration = (end - start) if end else None

        rrule_prop = component.get("RRULE")
        if rrule_prop is not None:
            # Expand the recurrence over our window.
            rule_text = rrule_prop.to_ical().decode()
            try:
                rule = rrulestr(rule_text, dtstart=start)
                occurrences = rule.between(now, window_end, inc=True)
            except (ValueError, TypeError):
                occurrences = [start] if now <= start <= window_end else []
            for occurrence in occurrences:
                occurrence = occurrence.replace(tzinfo=None)
                events.append(
                    RawEvent(
                        external_uid=f"{uid or title}:{occurrence.isoformat()}",
                        title=title,
                        start_time=occurrence,
                        end_time=occurrence + duration if duration else None,
                        description=description,
                        url=url,
                    )
                )
        else:
            if start < now - timedelta(days=1) or start > window_end:
                continue
            events.append(
                RawEvent(
                    external_uid=uid or f"{title}:{start.isoformat()}",
                    title=title,
                    start_time=start,
                    end_time=end,
                    description=description,
                    url=url,
                )
            )

    return events


class IcsAdapter:
    def fetch(self, source: EventSource) -> list[RawEvent]:
        if not source.url:
            raise ValueError(f"ICS source {source.id} has no URL")
        response = httpx.get(source.url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return parse_ics(response.text)
