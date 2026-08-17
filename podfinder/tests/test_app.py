"""End-to-end smoke tests against a temporary database."""

import os
import tempfile

# Point the app at a throwaway database before importing it.
_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PODFINDER_DB"] = f"sqlite:///{_db_file.name}"

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
    store = Store(slug="test-store", name="Test Store", city="Springfield", state="IL")
    session.add(store)
    session.flush()
    session.add(
        Event(
            store_id=store.id,
            external_uid="test-1",
            title="Commander Night",
            game="magic",
            format="commander",
            is_casual=True,
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
            start_time=datetime.now() + timedelta(days=2),
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
    assert len(data) == 1
    assert data[0]["title"] == "Commander Night"
    assert data[0]["store"]["slug"] == "test-store"


def test_store_pages(client):
    assert client.get("/stores").status_code == 200
    response = client.get("/stores/test-store")
    assert response.status_code == 200
    assert "Commander Night" in response.text
    assert client.get("/stores/nope").status_code == 404
