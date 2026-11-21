"""Deep scoring with a rubric; the final score is computed here, not by the LLM."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, field_validator

from . import prompts
from .llm import UNTRUSTED_NOTE, Claude
from .prefilter import PENDING_DAYS
from .store import Store, now_iso

log = logging.getLogger(__name__)

CATEGORIES = ["materials", "computing", "energy", "bio", "hardware", "software", "science", "other"]
TAG_CONTEXT_DAYS = 30
MAX_PROMPT_TAGS = 300
SUMMARY_CHARS = 1200


class GlossaryEntry(BaseModel):
    term: str
    meaning: str


class ItemScore(BaseModel):
    i: int
    novelty: float = Field(ge=0, le=10)
    impact: float = Field(ge=0, le=10)
    evidence: float = Field(ge=0, le=10)
    hype: float = Field(ge=0, le=10)
    category: str = "other"
    tags: list[str] = []
    why: str = ""
    plain: str = ""
    glossary: list[GlossaryEntry] = []

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in CATEGORIES else "other"


def parse_scores(data) -> list[ItemScore]:
    if isinstance(data, dict):  # tolerate {"items": [...]}
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        raise ValueError("expected a list")
    out = []
    for d in data:
        try:
            out.append(ItemScore.model_validate(d))
        except Exception:  # noqa: BLE001 - drop just the bad entry, it stays pending
            log.warning("dropping an invalid score entry")
    if data and not out:
        raise ValueError("no valid entries")
    return out


def normalize_tag(tag: str, aliases: dict[str, str]) -> str:
    t = re.sub(r"[^a-z0-9+#.]+", "-", tag.strip().lower()).strip("-")
    return aliases.get(t, t)


def final_score(s: ItemScore, n_sources: int, weight: float, w) -> float:
    raw = (w.novelty * s.novelty + w.impact * s.impact + w.evidence * s.evidence
           - w.hype * s.hype + w.per_extra_source * max(0, n_sources - 1))
    return round(max(0.0, min(10.0, raw)) * weight, 2)


def pending(store: Store, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=PENDING_DAYS)).isoformat(timespec="seconds")
    # Most-signal first, so a short budget goes to the best candidates.
    return store.db.execute(
        "SELECT i.* FROM items i JOIN prefilter p ON p.item_id=i.id AND p.keep=1 "
        "LEFT JOIN scores s ON s.item_id=i.id "
        "WHERE s.item_id IS NULL AND i.fetched_at>=? "
        "ORDER BY json_array_length(i.seen_in_sources) DESC, i.fetched_at DESC, i.id",
        (since,),
    ).fetchall()


def recent_tags(store: Store, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=TAG_CONTEXT_DAYS)).date().isoformat()
    rows = store.db.execute(
        "SELECT tag FROM tag_counts WHERE day>=? GROUP BY tag ORDER BY SUM(count) DESC LIMIT ?",
        (since, MAX_PROMPT_TAGS),
    ).fetchall()
    return [r[0] for r in rows]


def _signals(extra: dict) -> dict:
    keys = ("points", "comments", "stars", "stars_period", "language")
    return {k: extra[k] for k in keys if extra.get(k) not in (None, "")}


def _examples(cfg) -> str:
    parts = []
    for kind in ("good", "bad"):
        ex = cfg.examples.get(kind) or []
        if ex:
            parts.append(f"Examples of {kind.upper()} items for this reader:\n"
                         + "\n".join(f"- {e}" for e in ex))
    return ("\n" + "\n\n".join(parts) + "\n") if parts else ""


def system_prompt(cfg, tags: list[str]) -> str:
    return prompts.render(
        prompts.load("score"),
        profile=cfg.profile.strip(),
        rubric=(cfg.rubric or prompts.load("rubric")).strip(),
        examples=_examples(cfg),
        tags=", ".join(tags) or "(none yet)",
        categories=", ".join(CATEGORIES),
        untrusted=UNTRUSTED_NOTE,
    )


def run(store: Store, cfg, llm: Claude) -> int:
    """Score pending items. Returns how many were scored. LLM errors propagate after
    saving finished batches."""
    rows = pending(store)
    if not rows:
        return 0
    weights = {s.name: s.weight for s in cfg.sources}
    system = system_prompt(cfg, recent_tags(store))
    size = cfg.batching.score_batch
    scored = 0
    for start in range(0, len(rows), size):
        batch = rows[start:start + size]
        payload = []
        for n, r in enumerate(batch):
            payload.append({"i": n, "title": r["title"],
                            "sources": json.loads(r["seen_in_sources"]),
                            "summary": r["summary"][:SUMMARY_CHARS],
                            "signals": _signals(json.loads(r["extra"]))})
        out = llm.call_json(cfg.models.score, system, json.dumps(payload, ensure_ascii=False),
                            parse_scores)
        ts = now_iso()
        day = ts[:10]
        with store.db:
            for s in out:
                if not 0 <= s.i < len(batch):
                    continue
                r = batch[s.i]
                srcs = json.loads(r["seen_in_sources"])
                w = max((weights.get(x, 1.0) for x in srcs), default=1.0)
                tags = list(dict.fromkeys(t for t in (normalize_tag(t, cfg.tag_aliases)
                                                      for t in s.tags[:4]) if t))
                cur = store.db.execute(
                    "INSERT OR IGNORE INTO scores(item_id, novelty, impact, evidence, hype, score,"
                    " category, tags, why, plain, glossary, model, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], s.novelty, s.impact, s.evidence, s.hype,
                     final_score(s, len(srcs), w, cfg.scoring), s.category, json.dumps(tags),
                     s.why.strip(), s.plain.strip(),
                     json.dumps([g.model_dump() for g in s.glossary[:10]], ensure_ascii=False),
                     cfg.models.score, ts),
                )
                if cur.rowcount:
                    scored += 1
                    store.db.executemany(
                        "INSERT INTO tag_counts(tag, day, count) VALUES (?,?,1) "
                        "ON CONFLICT(tag, day) DO UPDATE SET count=count+1",
                        [(t, day) for t in tags],
                    )
        log.info("score batch: %d in, %d scored", len(batch), len(out))
    return scored
