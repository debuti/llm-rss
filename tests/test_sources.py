import httpx

from llmrss.config import HnSource
from llmrss.sources import fetch_all, github_trending, hn, rss


def test_rss(fixture):
    items = rss.parse("s", fixture("feed.xml"))
    assert [i.title for i in items] == ["Room-temperature & ambient-pressure thing", "Second"]
    assert items[0].summary == "Hello world"
    assert items[0].published_at.isoformat() == "2026-11-10T08:00:00+00:00"
    assert items[1].published_at is None


def test_hn(fixture):
    import json
    items = hn.parse("h", json.loads(fixture("hn.json")))
    assert len(items) == 2
    assert items[0].url == "https://github.com/a/b"
    assert items[1].url == "https://news.ycombinator.com/item?id=2"  # no external url
    assert items[1].summary == "text"


def test_hn_request():
    url, p = hn.build_request(HnSource(name="x", type="hn", list="show", min_points=5), now=1000000)
    assert url.endswith("search_by_date") and p["tags"] == "show_hn"
    assert p["numericFilters"] == "points>=5,created_at_i>827200"
    url, p = hn.build_request(HnSource(name="x", type="hn"))
    assert url.endswith("/search") and "numericFilters" not in p


def test_trending(fixture):
    items = github_trending.parse("g", fixture("trending.html").decode())
    assert items[0].url == "https://github.com/owner/repo"
    assert items[0].title == "owner/repo: A fast thing & more"
    assert items[0].extra == {"repo": "owner/repo", "stars": 12345, "stars_period": 1024,
                              "language": "Rust"}
    assert items[1].title == "o2/r2" and items[1].extra["stars"] is None


def test_fetch_all_isolates_failures(fixture):
    from llmrss.config import RssSource

    def handler(req):
        if "bad" in str(req.url):
            return httpx.Response(500)
        return httpx.Response(200, content=fixture("feed.xml"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    srcs = [RssSource(name="bad", type="rss", url="https://bad.test/"),
            RssSource(name="ok", type="rss", url="https://ok.test/"),
            RssSource(name="off", type="rss", url="https://bad.test/", enabled=False)]
    items, errors = fetch_all(srcs, client)
    assert len(items) == 2 and len(errors) == 1 and errors[0].startswith("source bad:")
