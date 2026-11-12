"""Cheap prefilter: titles only, batched, permissive (missing answer = keep)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from . import prompts
from .llm import UNTRUSTED_NOTE, Claude
from .store import Store, now_iso

log = logging.getLogger(__name__)
PENDING_DAYS = 3  # items older than this are not processed anymore


class PrefilterOut(BaseModel):
    drop: list[int]


def pending(store: Store, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=PENDING_DAYS)).isoformat(timespec="seconds")
    return store.db.execute(
        "SELECT i.* FROM items i LEFT JOIN prefilter p ON p.item_id=i.id "
        "WHERE p.item_id IS NULL AND i.fetched_at>=? ORDER BY i.fetched_at, i.id",
        (since,),
    ).fetchall()


def _save(store: Store, rows, keep: set[str], model: str | None) -> None:
    ts = now_iso()
    with store.db:
        store.db.executemany(
            "INSERT OR REPLACE INTO prefilter(item_id, keep, model, ts) VALUES (?,?,?,?)",
            [(r["id"], int(r["id"] in keep), model, ts) for r in rows],
        )


def run(store: Store, cfg, llm: Claude) -> tuple[int, int]:
    """Returns (processed, kept). Raises LLM errors after saving finished batches."""
    rows = pending(store)
    no_pf = {s.name for s in cfg.sources if not s.prefilter}
    passthrough = [r for r in rows if r["source"] in no_pf]
    _save(store, passthrough, {r["id"] for r in passthrough}, None)
    todo = [r for r in rows if r["source"] not in no_pf]

    system = prompts.render(prompts.load("prefilter"), profile=cfg.profile.strip(),
                            untrusted=UNTRUSTED_NOTE)
    processed, kept = len(passthrough), len(passthrough)
    size = cfg.batching.prefilter_batch
    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        payload = [{"i": n, "title": r["title"], "source": r["source"]} for n, r in enumerate(batch)]
        out = llm.call_json(cfg.models.prefilter, system, json.dumps(payload, ensure_ascii=False),
                            PrefilterOut.model_validate)
        drop = {n for n in out.drop if 0 <= n < len(batch)}
        keep = {r["id"] for n, r in enumerate(batch) if n not in drop}
        _save(store, batch, keep, cfg.models.prefilter)
        processed += len(batch)
        kept += len(keep)
        log.info("prefilter batch: %d in, %d kept", len(batch), len(keep))
    return processed, kept
