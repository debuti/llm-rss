import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest

from llmrss import prefilter, render, score, selection
from llmrss.llm import RateLimited
from llmrss.score import ItemScore, final_score, normalize_tag
from llmrss.sources import Item
from llmrss.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    s.ingest([Item("a", f"https://a.test/{n}", f"Title {n}", "sum") for n in range(3)]
             + [Item("b", "https://b.test/x", "Bee item")])
    return s


def test_prefilter_permissive_and_passthrough(store, cfg, fake_claude):
    # batch 1 (2 items): drop item 0 and an out-of-range index; batch 2: model omits everything
    llm = fake_claude([{"result": '{"drop": [0, 7]}'}, {"result": '{"drop": []}'}])
    n, kept = prefilter.run(store, cfg, llm)
    assert (n, kept) == (4, 3)
    assert fake_claude.calls() == 2       # source b skipped the LLM
    assert prefilter.pending(store) == []


def test_score_and_select(store, cfg, fake_claude):
    prefilter.run(store, cfg, fake_claude([{"echo_keep": True}]))
    n = score.run(store, cfg, fake_claude([{"echo_score": True}]))
    assert n == 4
    row = store.db.execute("SELECT * FROM scores LIMIT 1").fetchone()
    assert row["category"] == "hardware" and json.loads(row["tags"]) == ["risc-v", "open-hardware"]
    assert store.db.execute("SELECT count FROM tag_counts WHERE tag='risc-v'").fetchone()[0] == 4
    picked = selection.choose(store, cfg)
    assert len(picked) == 2                                    # max_per_day
    selection.publish(store, picked)
    assert selection.choose(store, cfg) == []                  # daily cap reached


def test_rate_limit_keeps_finished_batches(store, cfg, fake_claude):
    llm = fake_claude([{"result": '{"drop": []}'}, {"error": "rate"}])
    with pytest.raises(RateLimited):
        prefilter.run(store, cfg, llm)
    assert len(prefilter.pending(store)) == 1   # 1 passthrough + 2 done, 1 left


def test_final_score(cfg):
    s = ItemScore(i=0, novelty=10, impact=10, evidence=10, hype=0)
    assert final_score(s, 1, 1.0, cfg.scoring) == 10
    s = ItemScore(i=0, novelty=6, impact=6, evidence=6, hype=4)
    assert final_score(s, 1, 1.0, cfg.scoring) == 5.0
    assert final_score(s, 3, 1.0, cfg.scoring) == 6.0
    assert final_score(s, 1, 0.5, cfg.scoring) == 2.5


def test_normalize_tag():
    assert normalize_tag(" Solid State Batteries ", {}) == "solid-state-batteries"
    assert normalize_tag("ML", {"ml": "machine-learning"}) == "machine-learning"


def test_min_per_day_fills(store, cfg, fake_claude):
    cfg.selection.threshold = 10
    cfg.selection.min_per_day = 1
    prefilter.run(store, cfg, fake_claude([{"echo_keep": True}]))
    score.run(store, cfg, fake_claude([{"echo_score": True}]))
    assert len(selection.choose(store, cfg)) == 1


def test_render(store, cfg, fake_claude):
    prefilter.run(store, cfg, fake_claude([{"echo_keep": True}]))
    score.run(store, cfg, fake_claude([{"echo_score": True}]))
    old = datetime.now(timezone.utc) - timedelta(days=5)
    selection.publish(store, selection.choose(store, cfg), now=old)
    cfg.feed.keep_entries = 3
    preview = [r["item_id"] for r in store.db.execute(
        "SELECT item_id FROM scores WHERE item_id NOT IN (SELECT item_id FROM published)")]
    out = render.write_all(store, cfg, preview=preview[:1])
    root = ET.parse(out / "feed.xml").getroot()
    assert root.tag == "rss" and root.get("version") == "2.0"
    items = root.findall("channel/item")
    assert len(items) == 3
    assert items[0].find("guid").text == items[0].find("link").text
    assert "Score" in items[0].find("description").text
    cats = [c.text for c in items[0].findall("category")]
    assert cats == ["hardware", "risc-v", "open-hardware"]
    self_link = root.find("channel/{http://www.w3.org/2005/Atom}link").get("href")
    assert self_link == "https://u.github.io/r/feed.xml"
    assert "Title" in (out / "index.html").read_text()
