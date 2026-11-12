"""Weekly trends: tag momentum + an LLM note. Generated when >= 7 days since the last one."""

from __future__ import annotations

import html
import json
import logging
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from . import prompts
from .llm import UNTRUSTED_NOTE, Claude
from .store import Store

log = logging.getLogger(__name__)
PERIOD = timedelta(days=7)
TOLERANCE = timedelta(hours=12)   # scheduled runs drift
BASELINE_WEEKS = 4
MAX_ITEMS = 60
MAX_TAGS = 40


class Theme(BaseModel):
    name: str
    why: str
    items: list[int] = []


class WeeklyOut(BaseModel):
    headline: str
    themes: list[Theme]
    outlook: str = ""


def due(store: Store, now: datetime) -> bool:
    last = store.db.execute("SELECT MAX(ts) FROM weekly").fetchone()[0]
    if last is None:  # first one: wait until there is a week of data
        last = store.db.execute("SELECT MIN(ts) FROM runs").fetchone()[0]
        if last is None:
            return False
    return now - datetime.fromisoformat(last) >= PERIOD - TOLERANCE


def tag_stats(store: Store, today: date) -> list[dict]:
    week_start = (today - timedelta(days=6)).isoformat()
    base_start = (today - timedelta(days=6 + 7 * BASELINE_WEEKS)).isoformat()
    rows = store.db.execute(
        "SELECT tag, SUM(CASE WHEN day>=? THEN count ELSE 0 END) AS week,"
        " SUM(CASE WHEN day<? AND day>=? THEN count ELSE 0 END) AS base"
        " FROM tag_counts WHERE day>=? GROUP BY tag",
        (week_start, week_start, base_start, base_start),
    ).fetchall()
    # How many baseline weeks actually have data (a young DB shouldn't divide by 4).
    first = store.db.execute("SELECT MIN(day) FROM tag_counts").fetchone()[0]
    weeks = BASELINE_WEEKS
    if first and first > base_start:
        days = (date.fromisoformat(week_start) - date.fromisoformat(first)).days
        weeks = -(-days // 7) if days > 0 else 0   # partial weeks count as one
    out = []
    for r in rows:
        if not r["week"]:
            continue
        avg = r["base"] / weeks if weeks else None
        growth = (r["week"] + 1) / (avg + 1) if avg is not None else None
        out.append({"tag": r["tag"], "this_week": r["week"],
                    "prev_weekly_avg": round(avg, 1) if avg is not None else None,
                    "growth": round(growth, 2) if growth is not None else None})
    out.sort(key=lambda d: (d["growth"] or 1) * d["this_week"], reverse=True)
    return out[:MAX_TAGS]


def week_items(store: Store, now: datetime) -> list[dict]:
    since = (now - PERIOD).isoformat(timespec="seconds")
    rows = store.db.execute(
        "SELECT i.title, i.url, i.seen_in_sources, s.score, s.tags, s.why FROM scores s"
        " JOIN items i ON i.id=s.item_id WHERE s.ts>=? AND s.score IS NOT NULL"
        " ORDER BY s.score DESC LIMIT ?", (since, MAX_ITEMS)).fetchall()
    return [dict(r) for r in rows]


def to_html(out: WeeklyOut, items: list[dict], stats: list[dict]) -> str:
    esc = html.escape
    parts = []
    for t in out.themes:
        links = "".join(
            f'<li><a href="{esc(items[i]["url"])}">{esc(items[i]["title"])}</a></li>'
            for i in t.items if 0 <= i < len(items))
        parts.append(f"<h3>{esc(t.name)}</h3><p>{esc(t.why)}</p>"
                     + (f"<ul>{links}</ul>" if links else ""))
    if out.outlook:
        parts.append(f"<p><b>Outlook:</b> {esc(out.outlook)}</p>")
    rising = [s for s in stats if s["growth"] and s["growth"] >= 1.5][:10]
    if rising:
        parts.append("<p><b>Rising tags:</b> " + ", ".join(
            f"{esc(s['tag'])} ({s['this_week']}, ×{s['growth']:g})" for s in rising) + "</p>")
    return "".join(parts)


def run(store: Store, cfg, llm: Claude, now: datetime | None = None, force: bool = False,
        dry_run: bool = False) -> str | None:
    """Returns the week id written (or that would be written), or None if not due."""
    now = now or datetime.now(timezone.utc)
    if not force and not due(store, now):
        return None
    items = week_items(store, now)
    if not items:
        log.info("weekly: no scored items this week")
        return None
    stats = tag_stats(store, now.date())
    payload = {
        "tags": stats,
        "items": [{"i": n, "title": it["title"], "score": it["score"],
                   "tags": json.loads(it["tags"]), "sources": json.loads(it["seen_in_sources"]),
                   "why": it["why"]} for n, it in enumerate(items)],
    }
    system = prompts.render(prompts.load("weekly"), profile=cfg.profile.strip(),
                            untrusted=UNTRUSTED_NOTE)
    out = llm.call_json(cfg.models.weekly, system, json.dumps(payload, ensure_ascii=False),
                        WeeklyOut.model_validate)
    iso = now.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    title = f"{week}: {out.headline.strip()}"
    body = to_html(out, items, stats)
    if dry_run:
        print(title)
        return week
    with store.db:
        store.db.execute("INSERT OR REPLACE INTO weekly(week, ts, title, html) VALUES (?,?,?,?)",
                         (week, now.isoformat(timespec="seconds"), title, body))
    return week
