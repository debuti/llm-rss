"""Pick today's entries: threshold + daily cap (+ optional floor)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .prefilter import PENDING_DAYS
from .store import Store

FEED = "feed"


def published_today(store: Store, now: datetime) -> int:
    return store.db.execute(
        "SELECT COUNT(*) FROM published WHERE feed=? AND ts>=?", (FEED, now.date().isoformat())
    ).fetchone()[0]


def choose(store: Store, cfg, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=PENDING_DAYS)).isoformat(timespec="seconds")
    rows = store.db.execute(
        "SELECT s.item_id, s.score FROM scores s JOIN items i ON i.id=s.item_id "
        "LEFT JOIN published p ON p.item_id=s.item_id AND p.feed=? "
        "WHERE p.item_id IS NULL AND i.fetched_at>=? ORDER BY s.score DESC, i.fetched_at DESC",
        (FEED, since),
    ).fetchall()
    done = published_today(store, now)
    room = max(0, cfg.selection.max_per_day - done)
    picked = [r["item_id"] for r in rows if r["score"] >= cfg.selection.threshold][:room]
    floor = max(0, min(cfg.selection.min_per_day, cfg.selection.max_per_day) - done)
    if len(picked) < floor:
        rest = [r["item_id"] for r in rows if r["item_id"] not in picked]
        picked += rest[: floor - len(picked)]
    return picked


def publish(store: Store, ids: list[str], now: datetime | None = None) -> None:
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with store.db:
        store.db.executemany("INSERT OR IGNORE INTO published(item_id, feed, ts) VALUES (?,?,?)",
                             [(i, FEED, ts) for i in ids])
