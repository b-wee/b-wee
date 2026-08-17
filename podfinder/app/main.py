"""PodFinder — find casual events at your local game stores.

Run the dev server:

    uvicorn app.main:app --reload
"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from icalendar import Calendar as IcsCalendar
from icalendar import Event as IcsEvent
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.classify import FORMAT_LABELS, GAME_LABELS
from app.database import get_session, init_db
from app.geo import (
    DEFAULT_RADIUS_MILES,
    RADIUS_CHOICES,
    haversine_miles,
    parse_fee_cents,
    parse_location,
)
from app.models import Event, Store

BASE_DIR = Path(__file__).resolve().parent

DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# How often the background task *checks* for stale sources. Each source is
# only re-fetched once it's older than sync.MAX_AGE_HOURS (daily by default),
# so store feeds see at most one request per day regardless of app traffic.
SYNC_CHECK_SECONDS = 3600


async def _background_sync_loop() -> None:
    from app.ingest.sync import sync_all

    while True:
        try:
            refreshed = await asyncio.to_thread(sync_all)
            if refreshed:
                print(f"Background sync refreshed {refreshed} source(s).")
        except Exception as error:
            print(f"Background sync failed: {error}")
        await asyncio.sleep(SYNC_CHECK_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    sync_task = None
    if os.environ.get("PODFINDER_AUTO_SYNC", "1") == "1":
        sync_task = asyncio.create_task(_background_sync_loop())
    yield
    if sync_task:
        sync_task.cancel()


app = FastAPI(title="PodFinder", lifespan=lifespan)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Let clients/CDNs cache read endpoints — the data only changes when
    a source is re-synced (at most daily), so an hour of caching is safe."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/api/", "/static/")) or path == "/events.ics":
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(
    game_labels=GAME_LABELS,
    format_labels=FORMAT_LABELS,
    radius_choices=RADIUS_CHOICES,
    day_labels=DAY_LABELS,
)


def fmt_date(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}"


def fmt_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_time"] = fmt_time


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def query_events(
    session: Session,
    game: Optional[str] = None,
    format: Optional[str] = None,
    store_slug: Optional[str] = None,
    when: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    day: Optional[int] = None,
    casual_only: bool = False,
    free_only: bool = False,
    search: Optional[str] = None,
    origin: Optional[tuple[float, float]] = None,
    radius_miles: Optional[float] = None,
    sort: str = "time",
) -> list[Event]:
    """Fetch upcoming events matching the given filters.

    SQL narrows by time/category; distance, day-of-week, and fee filters
    run in Python (the dataset is small — a metro area's worth of stores).
    Every returned event gets a ``distance_miles`` attribute (None when no
    origin was given).
    """
    now = datetime.now()
    statement = (
        select(Event)
        .options(joinedload(Event.store))
        .where(Event.start_time >= now)
        .order_by(Event.start_time)
    )
    if game:
        statement = statement.where(Event.game == game)
    if format:
        statement = statement.where(Event.format == format)
    if store_slug:
        statement = statement.join(Store).where(Store.slug == store_slug)
    if casual_only:
        statement = statement.where(Event.is_casual.is_(True))
    if search:
        statement = statement.where(Event.title.ilike(f"%{search}%"))

    if when == "today":
        statement = statement.where(
            Event.start_time <= now.replace(hour=23, minute=59, second=59)
        )
    elif when == "week":
        statement = statement.where(Event.start_time <= now + timedelta(days=7))
    elif when == "weekend":
        # Saturday 00:00 through Sunday 23:59 of the current week.
        saturday = (now + timedelta(days=(5 - now.weekday()) % 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sunday_end = saturday + timedelta(days=1, hours=23, minutes=59)
        statement = statement.where(
            Event.start_time >= max(now, saturday), Event.start_time <= sunday_end
        )
    if date_from:
        statement = statement.where(
            Event.start_time >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        statement = statement.where(
            Event.start_time <= datetime.combine(date_to, datetime.max.time())
        )

    events = list(session.scalars(statement).unique().all())

    if day is not None:
        events = [event for event in events if event.start_time.weekday() == day]
    if free_only:
        events = [event for event in events if parse_fee_cents(event.entry_fee) == 0]

    for event in events:
        if origin and event.store.latitude is not None and event.store.longitude is not None:
            event.distance_miles = haversine_miles(
                origin[0], origin[1], event.store.latitude, event.store.longitude
            )
        else:
            event.distance_miles = None

    if origin and radius_miles:
        # Stores without coordinates can't match a radius search.
        events = [
            event
            for event in events
            if event.distance_miles is not None and event.distance_miles <= radius_miles
        ]

    if sort == "distance" and origin:
        events.sort(
            key=lambda event: (
                event.distance_miles if event.distance_miles is not None else 1e9,
                event.start_time,
            )
        )
    return events


def resolve_filters(
    game: Optional[str],
    format: Optional[str],
    store: Optional[str],
    when: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    day: Optional[str],
    casual: Optional[str],
    free: Optional[str],
    q: Optional[str],
    loc: Optional[str],
    radius: Optional[str],
    sort: Optional[str],
) -> dict:
    """Normalize raw query params into typed filter values."""
    origin = parse_location(loc)
    try:
        radius_miles = float(radius) if radius else DEFAULT_RADIUS_MILES
    except ValueError:
        radius_miles = DEFAULT_RADIUS_MILES
    try:
        day_index = int(day) if day not in (None, "") else None
    except ValueError:
        day_index = None
    if day_index is not None and not 0 <= day_index <= 6:
        day_index = None
    return {
        "game": game or None,
        "format": format or None,
        "store_slug": store or None,
        "when": when or None,
        "date_from": parse_date(date_from),
        "date_to": parse_date(date_to),
        "day": day_index,
        "casual_only": casual == "1",
        "free_only": free == "1",
        "search": q or None,
        "origin": origin,
        "radius_miles": radius_miles if origin else None,
        "sort": sort if sort in ("time", "distance") else "time",
    }


# Query params shared by the page, the API, and the ICS export.
FILTER_PARAMS = dict(
    game=Query(None),
    format=Query(None),
    store=Query(None),
    when=Query(None),
    date_from=Query(None),
    date_to=Query(None),
    day=Query(None),
    casual=Query(None),
    free=Query(None),
    q=Query(None),
    loc=Query(None),
    radius=Query(None),
    sort=Query(None),
)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: Session = Depends(get_session),
    game: Optional[str] = FILTER_PARAMS["game"],
    format: Optional[str] = FILTER_PARAMS["format"],
    store: Optional[str] = FILTER_PARAMS["store"],
    when: Optional[str] = FILTER_PARAMS["when"],
    date_from: Optional[str] = FILTER_PARAMS["date_from"],
    date_to: Optional[str] = FILTER_PARAMS["date_to"],
    day: Optional[str] = FILTER_PARAMS["day"],
    casual: Optional[str] = FILTER_PARAMS["casual"],
    free: Optional[str] = FILTER_PARAMS["free"],
    q: Optional[str] = FILTER_PARAMS["q"],
    loc: Optional[str] = FILTER_PARAMS["loc"],
    radius: Optional[str] = FILTER_PARAMS["radius"],
    sort: Optional[str] = FILTER_PARAMS["sort"],
    view: Optional[str] = Query(None),
):
    resolved = resolve_filters(
        game, format, store, when, date_from, date_to, day, casual, free, q,
        loc, radius, sort,
    )
    events = query_events(session, **resolved)
    stores = session.scalars(select(Store).order_by(Store.name)).all()

    # Pins for the map view: one per store that has matching events.
    map_pins: dict[int, dict] = {}
    for event in events:
        store_obj = event.store
        if store_obj.latitude is None or store_obj.longitude is None:
            continue
        pin = map_pins.setdefault(
            store_obj.id,
            {
                "name": store_obj.name,
                "slug": store_obj.slug,
                "lat": store_obj.latitude,
                "lon": store_obj.longitude,
                "events": [],
            },
        )
        if len(pin["events"]) < 5:
            pin["events"].append(
                {
                    "id": event.id,
                    "title": event.title,
                    "start": f"{fmt_date(event.start_time)} {fmt_time(event.start_time)}",
                }
            )

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": events,
            "stores": stores,
            "view": "map" if view == "map" else "list",
            "origin": resolved["origin"],
            "location_error": bool(loc) and resolved["origin"] is None,
            "map_pins": list(map_pins.values()),
            "query_string": request.url.query,
            "filters": {
                "game": game,
                "format": format,
                "store": store,
                "when": when,
                "date_from": date_from,
                "date_to": date_to,
                "day": day,
                "casual": casual,
                "free": free,
                "q": q,
                "loc": loc,
                "radius": radius or str(DEFAULT_RADIUS_MILES),
                "sort": sort or "time",
            },
        },
    )


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_detail(
    request: Request, event_id: int, session: Session = Depends(get_session)
):
    event = session.scalar(
        select(Event).options(joinedload(Event.store)).where(Event.id == event_id)
    )
    if event is None:
        return HTMLResponse("Event not found", status_code=404)
    return templates.TemplateResponse(request, "event_detail.html", {"event": event})


@app.get("/stores", response_class=HTMLResponse)
def store_list(request: Request, session: Session = Depends(get_session)):
    stores = session.scalars(select(Store).order_by(Store.city, Store.name)).all()
    return templates.TemplateResponse(request, "stores.html", {"stores": stores})


@app.get("/stores/{slug}", response_class=HTMLResponse)
def store_detail(request: Request, slug: str, session: Session = Depends(get_session)):
    store = session.scalar(select(Store).where(Store.slug == slug))
    if store is None:
        return HTMLResponse("Store not found", status_code=404)
    events = query_events(session, store_slug=slug)
    return templates.TemplateResponse(
        request, "store_detail.html", {"store": store, "events": events}
    )


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {})


# --- iCal export ------------------------------------------------------------


@app.get("/events.ics")
def events_ics(
    session: Session = Depends(get_session),
    game: Optional[str] = FILTER_PARAMS["game"],
    format: Optional[str] = FILTER_PARAMS["format"],
    store: Optional[str] = FILTER_PARAMS["store"],
    when: Optional[str] = FILTER_PARAMS["when"],
    date_from: Optional[str] = FILTER_PARAMS["date_from"],
    date_to: Optional[str] = FILTER_PARAMS["date_to"],
    day: Optional[str] = FILTER_PARAMS["day"],
    casual: Optional[str] = FILTER_PARAMS["casual"],
    free: Optional[str] = FILTER_PARAMS["free"],
    q: Optional[str] = FILTER_PARAMS["q"],
    loc: Optional[str] = FILTER_PARAMS["loc"],
    radius: Optional[str] = FILTER_PARAMS["radius"],
    sort: Optional[str] = FILTER_PARAMS["sort"],
):
    """Export the current filtered view as an iCal feed you can subscribe to."""
    resolved = resolve_filters(
        game, format, store, when, date_from, date_to, day, casual, free, q,
        loc, radius, sort,
    )
    events = query_events(session, **resolved)
    calendar = IcsCalendar()
    calendar.add("prodid", "-//PodFinder//podfinder//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "PodFinder events")
    for event in events:
        ics_event = IcsEvent()
        ics_event.add("uid", f"podfinder-{event.id}@podfinder.local")
        ics_event.add("summary", f"{event.title} @ {event.store.name}")
        ics_event.add("dtstart", event.start_time)
        if event.end_time:
            ics_event.add("dtend", event.end_time)
        location = ", ".join(
            part
            for part in [event.store.address, event.store.city, event.store.state]
            if part
        )
        if location:
            ics_event.add("location", location)
        if event.description:
            ics_event.add("description", event.description)
        if event.url:
            ics_event.add("url", event.url)
        calendar.add_component(ics_event)
    return Response(
        calendar.to_ical(),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="podfinder.ics"'},
    )


# --- JSON API ---------------------------------------------------------------


@app.get("/api/events")
def api_events(
    session: Session = Depends(get_session),
    game: Optional[str] = FILTER_PARAMS["game"],
    format: Optional[str] = FILTER_PARAMS["format"],
    store: Optional[str] = FILTER_PARAMS["store"],
    when: Optional[str] = FILTER_PARAMS["when"],
    date_from: Optional[str] = FILTER_PARAMS["date_from"],
    date_to: Optional[str] = FILTER_PARAMS["date_to"],
    day: Optional[str] = FILTER_PARAMS["day"],
    casual: Optional[str] = FILTER_PARAMS["casual"],
    free: Optional[str] = FILTER_PARAMS["free"],
    q: Optional[str] = FILTER_PARAMS["q"],
    loc: Optional[str] = FILTER_PARAMS["loc"],
    radius: Optional[str] = FILTER_PARAMS["radius"],
    sort: Optional[str] = FILTER_PARAMS["sort"],
):
    resolved = resolve_filters(
        game, format, store, when, date_from, date_to, day, casual, free, q,
        loc, radius, sort,
    )
    events = query_events(session, **resolved)
    return [
        {
            "id": event.id,
            "title": event.title,
            "game": event.game,
            "format": event.format,
            "is_casual": event.is_casual,
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "entry_fee": event.entry_fee,
            "url": event.url,
            "distance_miles": (
                round(event.distance_miles, 1)
                if event.distance_miles is not None
                else None
            ),
            "store": {
                "slug": event.store.slug,
                "name": event.store.name,
                "city": event.store.city,
                "state": event.store.state,
                "latitude": event.store.latitude,
                "longitude": event.store.longitude,
            },
        }
        for event in events
    ]


@app.get("/api/stores")
def api_stores(session: Session = Depends(get_session)):
    stores = session.scalars(select(Store).order_by(Store.name)).all()
    return [
        {
            "slug": store.slug,
            "name": store.name,
            "address": store.address,
            "city": store.city,
            "state": store.state,
            "website": store.website,
            "latitude": store.latitude,
            "longitude": store.longitude,
        }
        for store in stores
    ]
