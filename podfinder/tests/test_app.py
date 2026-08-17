"""End-to-end smoke tests against a temporary database."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models import Event, Store


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)  # fresh tables for every test
    init_db()
    session = SessionLocal()
    # Near store: Springfield, IL (ZIP 62701 is ~2 miles away).
    store = Store(
        slug="test-store",
        name="Test Store",
        city="Springfield",
        state="IL",
        latitude=39.7990,
        longitude=-89.6440,
    )
    # Far store: Bloomington, IL (~55 miles from Springfield).
    far_store = Store(
        slug="far-store",
        name="Far Store",
        city="Bloomington",
        state="IL",
        latitude=40.4842,
        longitude=-88.9937,
    )
    session.add_all([store, far_store])
    session.flush()
    session.add(
        Event(
            store_id=store.id,
            external_uid="test-1",
            title="Commander Night",
            game="magic",
            format="commander",
            is_casual=True,
            entry_fee="Free",
            start_time=datetime.now() + timedelta(days=1),
        )
    )
    session.add(
        Event(
            store_id=store.id,
            external_uid="test-2",
            title="Modern RCQ",
            game="magic",
            format="modern",
            is_casual=False,
            entry_fee="$25",
            start_time=datetime.now() + timedelta(days=2),
        )
    )
    session.add(
        Event(
            store_id=far_store.id,
            external_uid="test-3",
            title="Faraway Commander League",
            game="magic",
            format="commander",
            is_casual=True,
            entry_fee="$5",
            start_time=datetime.now() + timedelta(days=1, hours=2),
        )
    )
    session.commit()
    session.close()
    with TestClient(app) as test_client:
        yield test_client


def test_index_lists_events(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Commander Night" in response.text
    assert "Modern RCQ" in response.text


def test_casual_filter(client):
    response = client.get("/?casual=1")
    assert "Commander Night" in response.text
    assert "Modern RCQ" not in response.text


def test_format_filter(client):
    response = client.get("/?format=commander")
    assert "Commander Night" in response.text
    assert "Modern RCQ" not in response.text


def test_api_events(client):
    data = client.get("/api/events", params={"format": "commander"}).json()
    assert {event["title"] for event in data} == {
        "Commander Night",
        "Faraway Commander League",
    }
    assert data[0]["title"] == "Commander Night"
    assert data[0]["store"]["slug"] == "test-store"


def test_distance_filter_25_miles(client):
    response = client.get("/?loc=62701&radius=25")
    assert "Commander Night" in response.text
    assert "Faraway Commander League" not in response.text


def test_distance_filter_wider_radius_includes_far_store(client):
    response = client.get("/?loc=62701&radius=100")
    assert "Commander Night" in response.text
    assert "Faraway Commander League" in response.text


def test_distance_filter_latlon_input(client):
    # Raw "lat,lon" is what the "use my location" button submits.
    data = client.get(
        "/api/events", params={"loc": "39.7990,-89.6440", "radius": "25"}
    ).json()
    titles = {event["title"] for event in data}
    assert "Commander Night" in titles
    assert "Faraway Commander League" not in titles
    assert all(event["distance_miles"] is not None for event in data)


def test_sort_by_distance(client):
    data = client.get(
        "/api/events",
        params={"loc": "62701", "radius": "100", "sort": "distance", "format": "commander"},
    ).json()
    distances = [event["distance_miles"] for event in data]
    assert distances == sorted(distances)
    assert data[0]["title"] == "Commander Night"


def test_bad_location_shows_warning(client):
    response = client.get("/?loc=nowhere")
    assert response.status_code == 200
    assert "Couldn&#39;t find" in response.text or "Couldn't find" in response.text
    # An unresolvable location must not silently hide events.
    assert "Commander Night" in response.text


def test_free_only_filter(client):
    data = client.get("/api/events", params={"free": "1"}).json()
    assert {event["title"] for event in data} == {"Commander Night"}


def test_day_of_week_filter(client):
    tomorrow = (datetime.now() + timedelta(days=1)).weekday()
    data = client.get("/api/events", params={"day": str(tomorrow)}).json()
    titles = {event["title"] for event in data}
    assert "Commander Night" in titles
    assert "Modern RCQ" not in titles


def test_date_range_filter(client):
    only_tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    data = client.get(
        "/api/events", params={"date_from": only_tomorrow, "date_to": only_tomorrow}
    ).json()
    titles = {event["title"] for event in data}
    assert "Commander Night" in titles
    assert "Modern RCQ" not in titles


def test_when_presets_and_bad_params_dont_crash(client):
    for query in (
        "/?when=today",
        "/?when=week",
        "/?when=weekend",
        "/?loc=62701&radius=abc",   # bad radius falls back to default
        "/?day=99",                  # out-of-range day is ignored
        "/?date_from=not-a-date",
    ):
        assert client.get(query).status_code == 200


def test_api_responses_are_cacheable(client):
    # Data only changes on the daily source refresh, so read endpoints
    # advertise themselves as cacheable to clients/CDNs.
    response = client.get("/api/events")
    assert response.headers["cache-control"] == "public, max-age=3600"
    response = client.get("/events.ics")
    assert response.headers["cache-control"] == "public, max-age=3600"
    # HTML pages are not cached (they carry per-request filter state).
    assert "cache-control" not in client.get("/").headers


def test_map_view_renders(client):
    response = client.get("/?view=map&loc=62701&radius=100")
    assert response.status_code == 200
    assert "leaflet" in response.text
    assert "Test Store" in response.text


def test_event_detail_page(client):
    events = client.get("/api/events").json()
    response = client.get(f"/events/{events[0]['id']}")
    assert response.status_code == 200
    assert events[0]["title"] in response.text
    assert client.get("/events/999999").status_code == 404


def test_ics_export(client):
    response = client.get("/events.ics", params={"format": "commander"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert b"Commander Night" in response.content
    assert b"Modern RCQ" not in response.content


def test_store_pages(client):
    assert client.get("/stores").status_code == 200
    response = client.get("/stores/test-store")
    assert response.status_code == 200
    assert "Commander Night" in response.text
    assert client.get("/stores/nope").status_code == 404
