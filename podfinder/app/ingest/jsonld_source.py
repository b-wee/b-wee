"""Adapter for schema.org Event markup embedded in store websites.

Many store sites don't publish an ICS feed but do embed machine-readable
event data as ``<script type="application/ld+json">`` blocks — Squarespace,
Wix, and WordPress events plugins all do this automatically for SEO. Point
this adapter at a store's events page URL and it extracts every
schema.org Event it finds.
"""

import json
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Optional

import httpx
from dateutil import parser as dateparser

from app.ingest.base import RawEvent
from app.ingest.ics_source import LOOKAHEAD_DAYS
from app.models import EventSource


class _LdJsonExtractor(HTMLParser):
    """Collect the contents of every application/ld+json script tag."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_block = False
        self._buffer: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._in_block = True
            self._buffer = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_block:
            self._in_block = False
            self.blocks.append("".join(self._buffer))

    def handle_data(self, data):
        if self._in_block:
            self._buffer.append(data)


def _iter_nodes(node):
    """Yield every dict in arbitrarily nested JSON-LD (@graph, lists, ...)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)


def _is_event(node: dict) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return any(isinstance(t, str) and t.endswith("Event") for t in node_type)
    return isinstance(node_type, str) and node_type.endswith("Event")


def _parse_datetime(value) -> Optional[datetime]:
    """Parse an ISO-ish date string to a naive local datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dateparser.parse(value)
    except (ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_jsonld(html: str, now: Optional[datetime] = None) -> list[RawEvent]:
    """Extract schema.org Events from an HTML page."""
    now = now or datetime.now()
    window_end = now + timedelta(days=LOOKAHEAD_DAYS)

    extractor = _LdJsonExtractor()
    extractor.feed(html)

    events: list[RawEvent] = []
    seen_uids: set[str] = set()
    for block in extractor.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _iter_nodes(data):
            if not _is_event(node):
                continue
            title = node.get("name")
            if not isinstance(title, str) or not title.strip():
                continue
            start = _parse_datetime(node.get("startDate"))
            if start is None:
                continue
            if start < now - timedelta(days=1) or start > window_end:
                continue
            end = _parse_datetime(node.get("endDate"))
            description = node.get("description")
            if not isinstance(description, str):
                description = None
            url = node.get("url") or node.get("@id")
            if not isinstance(url, str):
                url = None
            uid = f"{node.get('@id') or url or title.strip()}:{start.isoformat()}"
            if uid in seen_uids:  # same event repeated across page blocks
                continue
            seen_uids.add(uid)
            events.append(
                RawEvent(
                    external_uid=uid,
                    title=title.strip(),
                    start_time=start,
                    end_time=end,
                    description=description,
                    url=url,
                )
            )
    return events


class JsonLdAdapter:
    def fetch(self, source: EventSource) -> list[RawEvent]:
        if not source.url:
            raise ValueError(f"JSON-LD source {source.id} has no URL")
        response = httpx.get(source.url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return parse_jsonld(response.text)
