from datetime import datetime

from app.ingest.jsonld_source import parse_jsonld

NOW = datetime(2026, 8, 17, 9, 0)

SINGLE_EVENT_PAGE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Event",
  "name": "Commander Night",
  "startDate": "2026-08-19T18:00:00",
  "endDate": "2026-08-19T21:00:00",
  "description": "Casual EDH pods, all decks welcome",
  "url": "https://example.com/events/commander-night"
}
</script>
</head><body>store page</body></html>
"""

GRAPH_PAGE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "LocalBusiness", "name": "The Dragon's Hoard"},
    {"@type": "SocialEvent", "name": "FNM Draft", "startDate": "2026-08-21T19:00:00"},
    {"@type": "Event", "name": "Old Event", "startDate": "2020-01-01T18:00:00"},
    {"@type": "Event", "name": "No Date Event"}
  ]
}
</script>
<script type="application/ld+json">not even json</script>
</head><body></body></html>
"""

TIMEZONE_PAGE = """
<script type="application/ld+json">
{"@type": "Event", "name": "Tz Event", "startDate": "2026-08-20T18:00:00-05:00"}
</script>
"""


def test_parses_single_event():
    events = parse_jsonld(SINGLE_EVENT_PAGE, now=NOW)
    assert len(events) == 1
    event = events[0]
    assert event.title == "Commander Night"
    assert event.start_time == datetime(2026, 8, 19, 18, 0)
    assert event.end_time == datetime(2026, 8, 19, 21, 0)
    assert event.description == "Casual EDH pods, all decks welcome"
    assert event.url == "https://example.com/events/commander-night"


def test_graph_page_extracts_only_valid_upcoming_events():
    events = parse_jsonld(GRAPH_PAGE, now=NOW)
    # LocalBusiness, past event, date-less event, and bad JSON all ignored;
    # SocialEvent (an Event subtype) is kept.
    assert [event.title for event in events] == ["FNM Draft"]


def test_timezone_normalized_to_naive():
    events = parse_jsonld(TIMEZONE_PAGE, now=NOW)
    assert len(events) == 1
    assert events[0].start_time.tzinfo is None


def test_duplicate_events_across_blocks_deduped():
    page = SINGLE_EVENT_PAGE + SINGLE_EVENT_PAGE
    events = parse_jsonld(page, now=NOW)
    assert len(events) == 1


def test_page_without_jsonld():
    assert parse_jsonld("<html><body>nothing here</body></html>", now=NOW) == []


def test_bad_dates_and_type_lists():
    page = """
    <script type="application/ld+json">
    [
      {"@type": ["Event", "Thing"], "name": "List Type Event", "startDate": "2026-08-20T18:00:00"},
      {"@type": "Event", "name": "Bad Date", "startDate": "whenever we feel like it"},
      {"@type": ["LocalBusiness"], "name": "Not An Event", "startDate": "2026-08-20T18:00:00"}
    ]
    </script>
    """
    events = parse_jsonld(page, now=NOW)
    assert [event.title for event in events] == ["List Type Event"]


def test_adapter_fetch(monkeypatch):
    from app.ingest import jsonld_source
    from app.models import EventSource

    class FakeResponse:
        text = SINGLE_EVENT_PAGE

        def raise_for_status(self):
            pass

    monkeypatch.setattr(jsonld_source.httpx, "get", lambda url, **kw: FakeResponse())
    adapter = jsonld_source.JsonLdAdapter()
    source = EventSource(id=1, store_id=1, kind="jsonld", url="https://example.com/events")
    events = adapter.fetch(source)
    assert [event.title for event in events] == ["Commander Night"]


def test_adapter_requires_url():
    import pytest

    from app.ingest.jsonld_source import JsonLdAdapter
    from app.models import EventSource

    with pytest.raises(ValueError):
        JsonLdAdapter().fetch(EventSource(id=1, store_id=1, kind="jsonld", url=None))
