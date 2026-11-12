import json
from datetime import datetime, timedelta, timezone

from llmrss.sources import Item
from llmrss.store import Store

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_dedup_by_url_and_title():
    s = Store(":memory:")
    r = s.ingest([Item("a", "https://x.test/1?utm_source=z", "Big News"),
                  Item("b", "http://www.x.test/1", "Other title"),        # same url
                  Item("c", "https://y.test/2", "Show HN: Big news!"),   # same title
                  Item("a", "https://x.test/1", "Big News")], now=T0)    # same source again
    assert len(r.new) == 1 and r.duplicates == 3 and r.cross_source == 2
    row = s.item(r.new[0])
    assert json.loads(row["seen_in_sources"]) == ["a", "b", "c"]
    assert row["url"] == "https://x.test/1"


def test_title_window():
    s = Store(":memory:")
    s.ingest([Item("a", "https://x.test/1", "Same")], now=T0)
    r = s.ingest([Item("b", "https://x.test/2", "Same")], now=T0 + timedelta(days=30))
    assert len(r.new) == 1


def test_prune_keeps_published():
    s = Store(":memory:")
    r = s.ingest([Item("a", "https://x.test/1", "One"), Item("a", "https://x.test/2", "Two")],
                 now=T0)
    s.db.execute("INSERT INTO published VALUES (?, 'feed', ?)", (r.new[0], T0.isoformat()))
    assert s.prune(10, now=T0 + timedelta(days=11)) == 1
    assert s.count("items") == 1


def test_migrations_idempotent(tmp_path):
    p = tmp_path / "db.sqlite"
    Store(p).close()
    s = Store(p)
    assert s.db.execute("PRAGMA user_version").fetchone()[0] == 1
    s.log_run(items_fetched=1, errors=["x"])
    assert s.count("runs") == 1
