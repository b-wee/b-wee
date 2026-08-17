"""PodFinder — find casual events at your local game stores.

Run the dev server:

    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.classify import FORMAT_LABELS, GAME_LABELS
from app.database import get_session, init_db
from app.models import Event, Store

BASE_DIR = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="PodFinder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(game_labels=GAME_LABELS, format_labels=FORMAT_LABELS)


def fmt_date(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}"


def fmt_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_time"] = fmt_time


def query_events(
    session: Session,
    game: Optional[str] = None,
    format: Optional[str] = None,
    store_slug: Optional[str] = None,
    when: Optional[str] = None,
    casual_only: bool = False,
    search: Optional[str] = None,
) -> list[Event]:
    """Fetch upcoming events matching the given filters, soonest first."""
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
        end = now.replace(hour=23, minute=59, second=59)
        statement = statement.where(Event.start_time <= end)
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

    return list(session.scalars(statement).unique().all())


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: Session = Depends(get_session),
    game: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    store: Optional[str] = Query(None),
    when: Optional[str] = Query(None),
    casual: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    events = query_events(
        session,
        game=game,
        format=format,
        store_slug=store,
        when=when,
        casual_only=casual == "1",
        search=q,
    )
    stores = session.scalars(select(Store).order_by(Store.name)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": events,
            "stores": stores,
            "filters": {
                "game": game,
                "format": format,
                "store": store,
                "when": when,
                "casual": casual,
                "q": q,
            },
        },
    )


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


# --- JSON API ---------------------------------------------------------------


@app.get("/api/events")
def api_events(
    session: Session = Depends(get_session),
    game: Optional[str] = None,
    format: Optional[str] = None,
    store: Optional[str] = None,
    when: Optional[str] = None,
    casual: Optional[bool] = None,
    q: Optional[str] = None,
):
    events = query_events(
        session,
        game=game,
        format=format,
        store_slug=store,
        when=when,
        casual_only=bool(casual),
        search=q,
    )
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
            "store": {
                "slug": event.store.slug,
                "name": event.store.name,
                "city": event.store.city,
                "state": event.store.state,
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
        }
        for store in stores
    ]
