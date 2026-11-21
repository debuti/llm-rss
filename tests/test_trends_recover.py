import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

from llmrss import prefilter, recover, render, score, selection, trends
from llmrss.sources import Item
from llmrss.store import Store, now_iso

NOW = datetime.now(timezone.utc)


def _scored_store(cfg, fake_claude):
    s = Store(":memory:")
    s.ingest([Item("a", f"https://a.test/{n}", f"Title {n}", "sum") for n in range(3)])
    prefilter.run(s, cfg, fake_claude([{"echo_keep": True}]))
    score.run(s, cfg, fake_claude([{"echo_score": True}]))
    return s


def test_weekly_due_logic(cfg, fake_claude):
    s = _scored_store(cfg, fake_claude)
    assert not trends.due(s, NOW)                       # no runs yet
    s.db.execute("INSERT INTO runs(ts) VALUES (?)", ((NOW - timedelta(days=3)).isoformat(),))
    assert not trends.due(s, NOW)
    assert trends.due(s, NOW + timedelta(days=4))


def test_weekly_generates_and_renders(cfg, fake_claude):
    s = _scored_store(cfg, fake_claude)
    out = {"headline": "Open silicon", "outlook": "watch <tapeouts>",
           "themes": [{"name": "RISC-V", "why": "More cores.", "items": [0, 1, 99]}]}
    week = trends.run(s, cfg, fake_claude([{"result": json.dumps(out)}]), force=True)
    assert week and week.startswith(str(NOW.isocalendar().year))
    xml = render.render_weekly(cfg, s).decode()
    assert "Open silicon" in xml and "&lt;tapeouts&gt;" in xml.replace("&amp;", "&")
    assert "weekly.xml#" + week in xml


def test_tag_stats_growth(cfg):
    s = Store(":memory:")
    today = NOW.date()
    rows = [("rust", today.isoformat(), 6), ("rust", (today - timedelta(days=10)).isoformat(), 2),
            ("old", (today - timedelta(days=10)).isoformat(), 5), ("new", today.isoformat(), 2)]
    s.db.executemany("INSERT INTO tag_counts VALUES (?,?,?)", rows)
    st = {d["tag"]: d for d in trends.tag_stats(s, today)}
    assert "old" not in st
    assert st["rust"]["this_week"] == 6 and st["rust"]["prev_weekly_avg"] == 2


def test_seed_roundtrip(cfg, fake_claude):
    s = _scored_store(cfg, fake_claude)
    selection.publish(s, selection.choose(s, cfg))
    s.db.execute("INSERT INTO weekly VALUES ('2026-W01', ?, 'T', '<p>x</p>')", (now_iso(),))
    feed = render.render_rss(cfg, render.entries(s, 100))
    weekly = render.render_weekly(cfg, s)

    fresh = Store(":memory:")
    assert recover.seed_from_feed(fresh, feed) == 2
    assert recover.seed_weekly(fresh, weekly) == 1
    assert fresh.count("published") == 2
    row = fresh.db.execute("SELECT * FROM scores").fetchone()
    assert row["score"] == 8.75 and row["why"] == "Because." and row["plain"] == "Simply put."
    assert json.loads(row["glossary"])[0]["term"] == "RISC-V"
    assert row["category"] == "hardware"
    assert json.loads(row["tags"]) == ["risc-v", "open-hardware"]
    # nothing gets republished, and the rebuilt feed matches
    assert selection.choose(fresh, cfg) == []
    again = render.render_rss(cfg, render.entries(fresh, 100))
    strip = lambda b: [l for l in b.decode().splitlines() if "lastBuildDate" not in l]
    assert strip(again) == strip(feed)
    # a re-fetched item is deduplicated against the seeded one
    assert fresh.ingest([Item("a", "https://a.test/0", "Title 0")]).new == []


def test_ensure_state_prefers_artifact(cfg, tmp_path, monkeypatch):
    db = tmp_path / "src.db"
    Store(db).close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(db, "llmrss.db")

    def handler(req):
        if req.url.path.endswith("/actions/artifacts"):
            return httpx.Response(200, json={"artifacts": [
                {"expired": False, "created_at": "2026-01-02", "archive_download_url": "https://dl.test/new"},
                {"expired": False, "created_at": "2026-01-01", "archive_download_url": "https://dl.test/old"}]})
        if str(req.url) == "https://dl.test/new":
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(404)

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "u/r")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert recover.ensure_state(cfg, client) == "artifact"
    assert recover.ensure_state(cfg, client) == "existing"


def test_ensure_state_seeds_from_pages(cfg, fake_claude, monkeypatch):
    s = _scored_store(cfg, fake_claude)
    selection.publish(s, selection.choose(s, cfg))
    feed = render.render_rss(cfg, render.entries(s, 100))
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def handler(req):
        if str(req.url) == "https://u.github.io/r/feed.xml":
            return httpx.Response(200, content=feed)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert recover.ensure_state(cfg, client) == "seeded (feed.xml: 2)"
