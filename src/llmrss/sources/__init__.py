"""Sources: each one is `fetch(cfg, client) -> list[Item]`, isolated from the others."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .. import USER_AGENT

log = logging.getLogger(__name__)


@dataclass
class Item:
    source: str
    url: str
    title: str
    summary: str = ""
    published_at: datetime | None = None
    extra: dict = field(default_factory=dict)


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def fetch_source(src, client: httpx.Client) -> list[Item]:
    from . import github_trending, hn, rss

    fetchers = {"rss": rss.fetch, "hn": hn.fetch, "github_trending": github_trending.fetch}
    return fetchers[src.type](src, client)


def fetch_all(sources, client: httpx.Client) -> tuple[list[Item], list[str]]:
    """Fetch every enabled source; a failing source is logged and skipped."""
    items: list[Item] = []
    errors: list[str] = []
    for src in sources:
        if not src.enabled:
            continue
        try:
            got = fetch_source(src, client)
            log.info("source %s: %d items", src.name, len(got))
            items.extend(got)
        except Exception as e:  # noqa: BLE001 - isolate sources
            msg = f"source {src.name}: {type(e).__name__}: {e}"
            log.warning(msg)
            errors.append(msg)
    return items, errors
