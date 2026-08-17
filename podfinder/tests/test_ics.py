from datetime import datetime, timedelta

from app.ingest.ics_source import parse_ics

SINGLE_EVENT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:event-1@example.com
SUMMARY:Commander Night
DESCRIPTION:Casual EDH pods
DTSTART:{start}
DTEND:{end}
END:VEVENT
END:VCALENDAR
"""

RECURRING_EVENT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:weekly-1@example.com
SUMMARY:Weekly Draft
DTSTART:{start}
DTEND:{end}
RRULE:FREQ=WEEKLY
END:VEVENT
END:VCALENDAR
"""


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def test_parses_single_upcoming_event():
    now = datetime(2026, 8, 17, 9, 0)
    start = now + timedelta(days=2)
    text = SINGLE_EVENT_ICS.format(
        start=stamp(start), end=stamp(start + timedelta(hours=3))
    )
    events = parse_ics(text, now=now)
    assert len(events) == 1
    assert events[0].title == "Commander Night"
    assert events[0].start_time == start
    assert events[0].end_time == start + timedelta(hours=3)
    assert events[0].external_uid == "event-1@example.com"


def test_skips_past_events():
    now = datetime(2026, 8, 17, 9, 0)
    start = now - timedelta(days=10)
    text = SINGLE_EVENT_ICS.format(
        start=stamp(start), end=stamp(start + timedelta(hours=3))
    )
    assert parse_ics(text, now=now) == []


def test_expands_weekly_recurrence():
    now = datetime(2026, 8, 17, 9, 0)
    start = now - timedelta(days=30)  # recurring series started a month ago
    text = RECURRING_EVENT_ICS.format(
        start=stamp(start), end=stamp(start + timedelta(hours=2))
    )
    events = parse_ics(text, now=now)
    # 45-day lookahead of a weekly event = 6-7 occurrences.
    assert 5 <= len(events) <= 7
    assert all(event.title == "Weekly Draft" for event in events)
    # UIDs must be unique per occurrence so upserts don't collide.
    uids = {event.external_uid for event in events}
    assert len(uids) == len(events)
    # Each occurrence keeps the original duration.
    assert all(
        event.end_time - event.start_time == timedelta(hours=2) for event in events
    )


TZ_EVENT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:tz-1@example.com
SUMMARY:UTC Commander Night
DTSTART:20260819T230000Z
END:VEVENT
END:VCALENDAR
"""

ALL_DAY_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:allday-1@example.com
SUMMARY:Prerelease Weekend
DTSTART;VALUE=DATE:20260822
END:VEVENT
END:VCALENDAR
"""


def test_timezone_aware_start_normalized_to_naive():
    events = parse_ics(TZ_EVENT_ICS, now=datetime(2026, 8, 17, 9, 0))
    assert len(events) == 1
    assert events[0].start_time.tzinfo is None


def test_all_day_event_lands_at_noon():
    events = parse_ics(ALL_DAY_ICS, now=datetime(2026, 8, 17, 9, 0))
    assert len(events) == 1
    assert events[0].start_time == datetime(2026, 8, 22, 12, 0)


def test_adapter_fetch(monkeypatch):
    from app.ingest import ics_source
    from app.models import EventSource

    class FakeResponse:
        text = SINGLE_EVENT_ICS.format(start="20991231T180000", end="20991231T210000")

        def raise_for_status(self):
            pass

    calls = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        return FakeResponse()

    monkeypatch.setattr(ics_source.httpx, "get", fake_get)
    adapter = ics_source.IcsAdapter()
    source = EventSource(id=1, store_id=1, kind="ics", url="https://example.com/cal.ics")
    adapter.fetch(source)  # events far in the future get windowed out; just exercise the path
    assert calls["url"] == "https://example.com/cal.ics"


def test_adapter_requires_url():
    import pytest

    from app.ingest.ics_source import IcsAdapter
    from app.models import EventSource

    with pytest.raises(ValueError):
        IcsAdapter().fetch(EventSource(id=1, store_id=1, kind="ics", url=None))
