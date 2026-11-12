"""Hacker News through the Algolia API."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..normalize import clean_text
from . import Item

API = "https://hn.algolia.com/api/v1/"
TAGS = {"front_page": "front_page", "show": "show_hn", "ask": "ask_hn", "story": "story"}
LOOKBACK_S = 2 * 86400


def build_request(src, now: float | None = None) -> tuple[str, dict]:
    params = {"tags": TAGS[src.list], "hitsPerPage": src.hits}
    filters = []
    if src.min_points:
        filters.append(f"points>={src.min_points}")
    if src.list == "front_page":
        return API + "search", params | ({"numericFilters": ",".join(filters)} if filters else {})
    since = int((now or time.time()) - LOOKBACK_S)
    filters.append(f"created_at_i>{since}")
    return API + "search_by_date", params | {"numericFilters": ",".join(filters)}


def parse(src_name: str, data: dict) -> list[Item]:
    items = []
    for h in data.get("hits", []):
        oid = h.get("objectID")
        title = h.get("title") or h.get("story_title")
        if not oid or not title:
            continue
        thread = f"https://news.ycombinator.com/item?id={oid}"
        url = h.get("url") or thread  # Ask/Show HN without external url -> thread
        ts = h.get("created_at_i")
        items.append(
            Item(
                source=src_name,
                url=url,
                title=clean_text(title, 500),
                summary=clean_text(h.get("story_text"), 2000),
                published_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                extra={"hn_id": oid, "points": h.get("points"),
                       "comments": h.get("num_comments"), "thread": thread},
            )
        )
    return items


def fetch(src, client) -> list[Item]:
    url, params = build_request(src)
    r = client.get(url, params=params)
    r.raise_for_status()
    return parse(src.name, r.json())
