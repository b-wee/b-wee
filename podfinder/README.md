# 🎲 PodFinder

Find casual events at your local game stores — Commander nights, drafts,
league nights, board game meetups — without checking five different store
calendars. A self-hostable replacement for spicerack.gg's event finder.

## How it works

```
store calendars (ICS feeds)          your browser
        │                                 ▲
        ▼                                 │
  python -m app.ingest.sync  ──►  SQLite  ──►  FastAPI web app + JSON API
  (poll feeds, expand recurring          (filter by game, format, date,
   events, classify titles)               store, casual-only)
```

1. Each store gets one or more **event sources** (see import methods below).
2. The **sync job** polls every feed, expands recurring events ("every
   Wednesday at 6pm"), and classifies each event by keyword — "Wednesday
   Night EDH" becomes *Magic: The Gathering / Commander / Casual*.
3. The **web app** shows everything in one filterable list *and* a map view,
   plus a JSON API (`/api/events`, `/api/stores`) if you want to build
   something on top.

## Features

- **Distance search** — enter a ZIP code (offline lookup, no API keys) or
  hit "📍 Near me" (browser geolocation), pick a radius (5–100 miles,
  default 25), and see how far each event is. Sort by soonest or nearest.
- **Filters** — game, format, date presets (today / next 7 days / this
  weekend), custom date range, day of week, casual-only, free-only, and
  free-text search.
- **Map view** — matching stores plotted on an OpenStreetMap/Leaflet map,
  with their upcoming events in each pin's popup.
- **Event pages** — every event links to a detail page with time, store
  address, and a map link.
- **iCal export** — `/events.ics` honors every filter, so you can subscribe
  to "casual Commander within 25 miles" from your phone's calendar app.

## Import methods

Three source kinds cover most local game stores:

| Kind | What it reads | Typical stores |
|---|---|---|
| `ics` | An iCal/ICS calendar feed URL, with recurring-event (RRULE) expansion | Google Calendar, Squarespace, Wix — anything with a "subscribe to calendar" link |
| `jsonld` | schema.org `Event` markup embedded in a store's events page (`<script type="application/ld+json">`) | Squarespace, Wix, and WordPress event plugins emit this automatically for SEO; works even when no feed is advertised |
| manual | `weekly_events` entries in `data/stores.sample.json` ("every Wednesday at 6pm") | Stores whose schedule only exists as a flyer on the door |

Prefer `ics` when a store has one (it's the most reliable), fall back to
`jsonld` for stores that only have an events web page, and use manual
entries as the last resort. Facebook events and Wizards EventLink have no
public feed/API, so they'd need per-store arrangements — the adapter
interface in `app/ingest/base.py` is the extension point if you get access.

## Data freshness & caching at scale

Browsing **never** hits store websites. All reads (pages, `/api/*`,
`/events.ics`) are served from the local database, which acts as a cache
of the upstream feeds:

- Each source is re-fetched **at most once every 24 hours**
  (`MAX_AGE_HOURS` in `app/ingest/sync.py`). A background task inside the
  app checks hourly for sources whose cache has gone stale and refreshes
  only those — so store feeds see one request per day per source, no
  matter how many people are browsing.
- `python -m app.ingest.sync` respects the same freshness window (making
  an external cron job a safe alternative); `--force` refreshes
  everything immediately.
- Set `PODFINDER_AUTO_SYNC=0` to disable the in-app scheduler if you'd
  rather drive syncs from cron alone.
- Read endpoints send `Cache-Control: public, max-age=3600`, so a CDN or
  reverse proxy in front of the app can absorb repeated queries too.

## Quick start

```bash
cd podfinder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m app.seed          # load sample stores + 4 weeks of sample events
uvicorn app.main:app --reload
```

Open http://localhost:8000 — you'll see the sample events, filterable by
game, format, date, and store.

## Adding your real local stores

1. Edit `data/stores.sample.json`: replace the sample stores with your real
   ones. For each store, find its public calendar's ICS URL and add it:

   ```json
   "sources": [{"kind": "ics", "url": "https://calendar.google.com/calendar/ical/…/basic.ics"}]
   ```

   *Finding the ICS URL:* on Google Calendar it's under calendar settings →
   "Public address in iCal format"; Squarespace and Wix event pages expose
   an `?format=ical` / ICS export link.

2. Load the stores and sync their feeds:

   ```bash
   python -m app.seed
   python -m app.ingest.sync
   ```

3. That's it — while the app is running it refreshes each source
   automatically once a day. If you'd rather control the schedule
   yourself, set `PODFINDER_AUTO_SYNC=0` and run the sync from cron
   (it's idempotent, and skips sources fresher than 24h anyway):

   ```
   0 * * * * cd /path/to/podfinder && .venv/bin/python -m app.ingest.sync
   ```

## Project layout

```
app/
  main.py           FastAPI routes (web pages + JSON API)
  models.py         SQLAlchemy models: Store, EventSource, Event
  classify.py       keyword classifier: title → game / format / casual
  geo.py            haversine distance, offline ZIP geocoding, fee parsing
  seed.py           load stores.sample.json + generate sample events
  ingest/
    base.py         adapter interface (RawEvent, SourceAdapter)
    ics_source.py   ICS/iCal feed adapter (handles recurring events)
    jsonld_source.py  schema.org Event scraper for stores without feeds
    sync.py         daily cache refresh of all enabled sources
  templates/        Jinja2 HTML templates
  static/           CSS
data/
  stores.sample.json  store list + sample weekly events
tests/              pytest suite (classifier, ICS parsing, app routes)
```

## Running tests

```bash
python -m pytest
```

## Ideas for what to build next

- **More adapters**: many stores post events only on Facebook or
  Wizards EventLink — each is a new adapter in `app/ingest/`.
- **Email digest**: "here's this week's Commander events near you."
- **RSVP / attendance**: let players mark "I'm going" so pods actually fire.
