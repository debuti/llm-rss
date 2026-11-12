"""SQLite state: schema, simple migrations, dedup."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .normalize import canonical_url, title_hash, url_id
from .sources import Item

# Each entry migrates from version i to i+1. Append only, never edit.
MIGRATIONS = [
    """
    CREATE TABLE items(
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        fetched_at TEXT NOT NULL,
        title_hash TEXT NOT NULL,
        seen_in_sources TEXT NOT NULL DEFAULT '[]',
        extra TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX items_title_hash ON items(title_hash);
    CREATE INDEX items_fetched_at ON items(fetched_at);
    CREATE TABLE prefilter(
        item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        keep INTEGER NOT NULL, model TEXT, ts TEXT NOT NULL
    );
    CREATE TABLE scores(
        item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        novelty REAL, impact REAL, evidence REAL, hype REAL, score REAL,
        category TEXT, tags TEXT NOT NULL DEFAULT '[]', why TEXT, model TEXT, ts TEXT NOT NULL
    );
    CREATE TABLE published(
        item_id TEXT NOT NULL, feed TEXT NOT NULL, ts TEXT NOT NULL,
        PRIMARY KEY(item_id, feed)
    );
    CREATE TABLE tag_counts(
        tag TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY(tag, day)
    );
    CREATE TABLE runs(
        ts TEXT NOT NULL, items_fetched INTEGER, new INTEGER, prefiltered INTEGER,
        scored INTEGER, published INTEGER, llm_calls INTEGER, errors TEXT
    );
    """,
    """
    CREATE TABLE weekly(
        week TEXT PRIMARY KEY, ts TEXT NOT NULL, title TEXT NOT NULL, html TEXT NOT NULL
    );
    """,
]

DEDUP_TITLE_WINDOW = timedelta(days=14)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class IngestResult:
    new: list[str]          # ids of newly inserted items
    duplicates: int         # already known (same url or same title)
    cross_source: int       # duplicates seen in a new source (signal)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.db.close()

    def migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(MIGRATIONS)):
            with self.db:
                self.db.executescript(MIGRATIONS[i])
                self.db.execute(f"PRAGMA user_version={i + 1}")

    # --- items -----------------------------------------------------------

    def _add_source(self, row: sqlite3.Row, source: str) -> bool:
        seen = json.loads(row["seen_in_sources"])
        if source in seen:
            return False
        seen.append(source)
        self.db.execute("UPDATE items SET seen_in_sources=? WHERE id=?",
                        (json.dumps(seen), row["id"]))
        return True

    def ingest(self, items: list[Item], now: datetime | None = None) -> IngestResult:
        now = now or datetime.now(timezone.utc)
        ts = now.isoformat(timespec="seconds")
        window_start = (now - DEDUP_TITLE_WINDOW).isoformat(timespec="seconds")
        new, dups, cross = [], 0, 0
        with self.db:
            for it in items:
                iid = url_id(it.url)
                th = title_hash(it.title)
                row = self.db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
                if row is None:
                    row = self.db.execute(
                        "SELECT * FROM items WHERE title_hash=? AND fetched_at>=? LIMIT 1",
                        (th, window_start),
                    ).fetchone()
                if row is not None:
                    dups += 1
                    cross += self._add_source(row, it.source)
                    continue
                self.db.execute(
                    "INSERT INTO items(id, source, url, title, summary, published_at, fetched_at,"
                    " title_hash, seen_in_sources, extra) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (iid, it.source, canonical_url(it.url), it.title, it.summary,
                     it.published_at.isoformat() if it.published_at else None, ts, th,
                     json.dumps([it.source]), json.dumps(it.extra, default=str)),
                )
                new.append(iid)
        return IngestResult(new, dups, cross)

    def item(self, iid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()

    def count(self, table: str) -> int:
        assert table.isidentifier()
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # --- maintenance -----------------------------------------------------

    def prune(self, retention_days: int, now: datetime | None = None) -> int:
        """Delete old items that never made it to a feed."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self.db:
            cur = self.db.execute(
                "DELETE FROM items WHERE fetched_at<? AND id NOT IN (SELECT item_id FROM published)",
                (cutoff,),
            )
            self.db.execute("DELETE FROM tag_counts WHERE day<?", (cutoff[:10],))
        return cur.rowcount

    def log_run(self, **kw) -> None:
        cols = ["items_fetched", "new", "prefiltered", "scored", "published", "llm_calls", "errors"]
        vals = [kw.get(c) for c in cols]
        if isinstance(vals[-1], list):
            vals[-1] = json.dumps(vals[-1])
        with self.db:
            self.db.execute(
                f"INSERT INTO runs(ts, {', '.join(cols)}) VALUES (?{',?' * len(cols)})",
                [now_iso(), *vals],
            )
