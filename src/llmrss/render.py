"""Render public/feed.xml and public/index.html from the database."""

from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from .selection import FEED
from .store import Store

ATOM = "http://www.w3.org/2005/Atom"


def _dt(iso: str) -> datetime:
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def entries(store: Store, limit: int, preview: list[str] = ()) -> list[dict]:
    """Published entries (newest first), plus `preview` ids on top (dry-run)."""
    q = ("SELECT i.*, s.novelty, s.impact, s.evidence, s.hype, s.score, s.category, s.tags, s.why,"
         " s.plain, s.glossary,"
         " {ts} AS pub_ts FROM items i JOIN scores s ON s.item_id=i.id {join} {where}"
         " ORDER BY pub_ts DESC, s.score DESC LIMIT ?")
    out = []
    if preview:
        marks = ",".join("?" * len(preview))
        out += store.db.execute(
            q.format(ts="datetime('now')", join="", where=f"WHERE i.id IN ({marks})"),
            [*preview, limit]).fetchall()
    out += store.db.execute(
        q.format(ts="p.ts", join="JOIN published p ON p.item_id=i.id AND p.feed=?", where=""),
        (FEED, max(0, limit - len(out)))).fetchall()
    return [dict(r) for r in out]


def _fmt(x) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) else "?"


def description(e: dict) -> str:
    srcs = json.loads(e["seen_in_sources"])
    extra = json.loads(e["extra"])
    parts = []
    if e.get("plain"):
        parts.append(f'<p class="plain"><b>In plain words:</b> {html.escape(e["plain"])}</p>')
    parts.append(f'<p class="why">{html.escape(e["why"] or "")}</p>')
    gloss = json.loads(e.get("glossary") or "[]")
    if gloss:
        parts.append('<ul class="glossary">' + "".join(
            f"<li><b>{html.escape(g['term'])}</b>: {html.escape(g['meaning'])}</li>"
            for g in gloss) + "</ul>")
    parts += [f"<p><b>Score {_fmt(e['score'])}</b> · novelty {_fmt(e['novelty'])} · impact "
             f"{_fmt(e['impact'])} · evidence {_fmt(e['evidence'])} · hype {_fmt(e['hype'])}</p>"]
    seen = f"Seen in: {html.escape(', '.join(srcs))}"
    if extra.get("thread") and extra["thread"] != e["url"]:
        seen += f' · <a href="{html.escape(extra["thread"])}">HN discussion</a>'
    parts.append(f"<p>{seen}</p>")
    return "".join(parts)


def _ns() -> None:
    ET.register_namespace("atom", ATOM)


def render_rss(cfg, items: list[dict], self_name: str = "feed.xml") -> bytes:
    _ns()
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    link = cfg.feed.link if cfg.feed.link.endswith("/") else cfg.feed.link + "/"
    ET.SubElement(ch, "title").text = cfg.feed.title
    ET.SubElement(ch, "link").text = link
    ET.SubElement(ch, "description").text = cfg.feed.description
    ET.SubElement(ch, "language").text = "en"
    ET.SubElement(ch, f"{{{ATOM}}}link", href=link + self_name, rel="self",
                  type="application/rss+xml")
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(ch, "generator").text = "llmrss"
    for e in items:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = e["title"]
        ET.SubElement(it, "link").text = e["url"]
        ET.SubElement(it, "guid", isPermaLink="true").text = e["url"]
        ET.SubElement(it, "pubDate").text = format_datetime(_dt(e["pub_ts"]))
        ET.SubElement(it, "description").text = description(e)
        ET.SubElement(it, "category").text = e["category"]
        for t in json.loads(e["tags"]):
            ET.SubElement(it, "category", domain="tag").text = t
    ET.indent(rss)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")


def render_weekly(cfg, store: Store, limit: int = 52) -> bytes:
    _ns()
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    link = cfg.feed.link if cfg.feed.link.endswith("/") else cfg.feed.link + "/"
    ET.SubElement(ch, "title").text = f"{cfg.feed.title}: weekly trends"
    ET.SubElement(ch, "link").text = link
    ET.SubElement(ch, "description").text = "What is emerging, week by week"
    ET.SubElement(ch, "language").text = "en"
    ET.SubElement(ch, f"{{{ATOM}}}link", href=link + "weekly.xml", rel="self",
                  type="application/rss+xml")
    ET.SubElement(ch, "generator").text = "llmrss"
    for w in store.db.execute("SELECT * FROM weekly ORDER BY ts DESC LIMIT ?", (limit,)):
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = w["title"]
        ET.SubElement(it, "link").text = link
        ET.SubElement(it, "guid", isPermaLink="false").text = f"{link}weekly.xml#{w['week']}"
        ET.SubElement(it, "pubDate").text = format_datetime(_dt(w["ts"]))
        ET.SubElement(it, "description").text = w["html"]
    ET.indent(rss)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")


def render_index(cfg, items: list[dict], last_run: dict | None) -> str:
    esc = html.escape
    run = "never"
    if last_run:
        run = (f"{esc(last_run['ts'])} UTC: {last_run['items_fetched'] or 0} fetched, "
               f"{last_run['new'] or 0} new, {last_run['published'] or 0} published")
    rows = "\n".join(
        f'<li><a href="{esc(e["url"])}">{esc(e["title"])}</a> <small>({_fmt(e["score"])})</small>'
        + (f"<br>{esc(e['plain'])}" if e.get("plain") else "")
        + f"<br><small>{esc(e['why'] or '')}</small></li>"
        for e in items[:30]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(cfg.feed.title)}</title>
<link rel="alternate" type="application/rss+xml" title="{esc(cfg.feed.title)}" href="feed.xml">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}}
li{{margin:.8rem 0}}small{{color:#666}}</style></head>
<body><h1>{esc(cfg.feed.title)}</h1><p>{esc(cfg.feed.description)}</p>
<p><a href="feed.xml">Daily feed (RSS)</a> · <a href="weekly.xml">Weekly trends (RSS)</a></p>
<p><small>Last run: {run}</small></p>
<ol>
{rows}
</ol></body></html>
"""


def write_all(store: Store, cfg, preview: list[str] = ()) -> Path:
    out = Path(cfg.public_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = entries(store, cfg.feed.keep_entries, preview)
    (out / "feed.xml").write_bytes(render_rss(cfg, items))
    (out / "weekly.xml").write_bytes(render_weekly(cfg, store))
    last = store.db.execute("SELECT * FROM runs ORDER BY ts DESC LIMIT 1").fetchone()
    (out / "index.html").write_text(render_index(cfg, items, dict(last) if last else None),
                                    encoding="utf-8")
    return out
