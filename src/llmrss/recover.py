"""Recover the state when the Actions cache is gone.

1. Latest `llmrss-db` artifact of this repo (needs GH_TOKEN + GITHUB_REPOSITORY).
2. Otherwise, seed `published` from the feeds already on Pages, so the feed is neither
   duplicated nor lost (trends history is lost).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import xml.etree.ElementTree as ET

from .normalize import canonical_url, clean_text, title_hash, url_id
from .selection import FEED
from .store import Store

log = logging.getLogger(__name__)
ARTIFACT = "llmrss-db"
DB_NAME = "llmrss.db"


def from_artifact(state_dir: Path, client: httpx.Client, env=None) -> bool:
    env = os.environ if env is None else env
    token, repo = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"), env.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = client.get(f"https://api.github.com/repos/{repo}/actions/artifacts",
                   params={"name": ARTIFACT, "per_page": 20}, headers=h)
    r.raise_for_status()
    arts = [a for a in r.json().get("artifacts", []) if not a.get("expired")]
    if not arts:
        return False
    arts.sort(key=lambda a: a["created_at"], reverse=True)
    z = client.get(arts[0]["archive_download_url"], headers=h)
    z.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        if DB_NAME not in zf.namelist():
            return False
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / DB_NAME).write_bytes(zf.read(DB_NAME))
    return True


_SCORE_RE = re.compile(r"Score ([\d.]+)")
_SUB_RE = re.compile(r"(novelty|impact|evidence|hype) ([\d.]+)")
_SEEN_RE = re.compile(r"Seen in: ([^<·]+)")


def seed_from_feed(store: Store, xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    n = 0
    with store.db:
        for it in root.findall("channel/item"):
            url = (it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            if not url or not title:
                continue
            desc = it.findtext("description") or ""
            pub = it.findtext("pubDate")
            ts = (parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat(timespec="seconds")
                  if pub else "1970-01-01T00:00:00+00:00")
            category = next((c.text for c in it.findall("category") if not c.get("domain")), "other")
            tags = [c.text for c in it.findall("category") if c.get("domain") == "tag" and c.text]
            m = _SEEN_RE.search(desc)
            seen = [s.strip() for s in m.group(1).split(",")] if m else ["recovered"]
            why_m = re.match(r"\s*<p>(.*?)</p>", desc, re.S)
            m = _SCORE_RE.search(desc)
            sub = {k: float(v) for k, v in _SUB_RE.findall(desc)}
            iid = url_id(url)
            store.db.execute(
                "INSERT OR IGNORE INTO items(id, source, url, title, summary, published_at,"
                " fetched_at, title_hash, seen_in_sources) VALUES (?,?,?,?,'',?,?,?,?)",
                (iid, seen[0], canonical_url(url), title, ts, ts, title_hash(title), json.dumps(seen)))
            store.db.execute("INSERT OR IGNORE INTO prefilter VALUES (?,1,'recovered',?)", (iid, ts))
            store.db.execute(
                "INSERT OR IGNORE INTO scores(item_id, novelty, impact, evidence, hype, score,"
                " category, tags, why, model, ts) VALUES (?,?,?,?,?,?,?,?,?,'recovered',?)",
                (iid, sub.get("novelty"), sub.get("impact"), sub.get("evidence"), sub.get("hype"),
                 float(m.group(1)) if m else None, category or "other", json.dumps(tags),
                 clean_text(why_m.group(1)) if why_m else "", ts))
            store.db.execute("INSERT OR IGNORE INTO published VALUES (?,?,?)", (iid, FEED, ts))
            n += 1
    return n


def seed_weekly(store: Store, xml_bytes: bytes) -> int:
    root = ET.fromstring(xml_bytes)
    n = 0
    with store.db:
        for it in root.findall("channel/item"):
            guid = it.findtext("guid") or ""
            week = guid.rsplit("#", 1)[-1]
            pub = it.findtext("pubDate")
            if not re.fullmatch(r"\d{4}-W\d{2}", week) or not pub:
                continue
            ts = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat(timespec="seconds")
            store.db.execute("INSERT OR IGNORE INTO weekly(week, ts, title, html) VALUES (?,?,?,?)",
                             (week, ts, it.findtext("title") or week, it.findtext("description") or ""))
            n += 1
    return n


def ensure_state(cfg, client: httpx.Client) -> str:
    """Make sure a DB exists before opening it. Returns what happened."""
    state_dir = Path(cfg.state_dir)
    if (state_dir / DB_NAME).exists():
        return "existing"
    try:
        if from_artifact(state_dir, client):
            return "artifact"
    except Exception as e:  # noqa: BLE001
        log.warning("artifact recovery failed: %s", type(e).__name__)

    store = Store(state_dir / DB_NAME)
    try:
        base = cfg.feed.link if cfg.feed.link.endswith("/") else cfg.feed.link + "/"
        seeded = []
        for name, fn in (("feed.xml", seed_from_feed), ("weekly.xml", seed_weekly)):
            try:
                r = client.get(base + name)
                if r.status_code == 200:
                    seeded.append(f"{name}: {fn(store, r.content)}")
            except Exception as e:  # noqa: BLE001
                log.warning("seeding from %s failed: %s", name, type(e).__name__)
    finally:
        store.close()
    return "seeded (" + ", ".join(seeded) + ")" if seeded else "new"
