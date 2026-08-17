"""Vercel serverless entrypoint (demo hosting).

Vercel's Python runtime serves the ASGI ``app`` exported here. Two things
differ from a normal deployment:

- The filesystem is read-only except ``/tmp``, so the SQLite cache lives
  there. It's ephemeral per instance, which is fine for a demo.
- On cold start we seed the sample stores/events if the database is
  empty, so the demo always shows four weeks of upcoming events.

Feed syncing stays off (`PODFINDER_AUTO_SYNC=0`) — serverless instances
are too short-lived for the background scheduler; a real deployment
would run ``python -m app.ingest.sync`` from a cron/scheduled function.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PODFINDER_DB", "sqlite:////tmp/podfinder.db")
os.environ.setdefault("PODFINDER_AUTO_SYNC", "0")

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Store  # noqa: E402

init_db()
_session = SessionLocal()
try:
    if _session.scalar(select(Store)) is None:
        from app.seed import seed

        seed()
finally:
    _session.close()

from app.main import app  # noqa: E402,F401  (Vercel serves this ASGI app)
