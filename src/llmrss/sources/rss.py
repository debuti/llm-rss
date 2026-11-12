"""Generic RSS/Atom source (Hackaday, Lobsters, arXiv, blogs...)."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser

from ..normalize import clean_text
from . import Item


def _date(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def parse(src_name: str, content: bytes) -> list[Item]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"unparseable feed: {feed.bozo_exception!r}")
    items = []
    for e in feed.entries:
        url = e.get("link") or e.get("id")
        title = clean_text(e.get("title"), 500)
        if not url or not title:
            continue
        summary = e.get("summary") or ""
        if not summary and e.get("content"):
            summary = e["content"][0].get("value", "")
        extra = {}
        if e.get("comments"):
            extra["comments"] = e["comments"]
        items.append(Item(src_name, url, title, clean_text(summary), _date(e), extra))
    return items


def fetch(src, client) -> list[Item]:
    r = client.get(src.url)
    r.raise_for_status()
    return parse(src.name, r.content)
