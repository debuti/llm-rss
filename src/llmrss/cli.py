"""llmrss CLI: run | weekly | validate-config | rebuild-feed."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config as config_mod
from . import prefilter, recover, render, score, selection, trends
from .llm import AuthError, BudgetExhausted, Claude, LLMError, RateLimited
from .sources import fetch_all, fetch_source, make_client
from .store import Store

log = logging.getLogger("llmrss")


def _load_config():
    try:
        return config_mod.load()
    except config_mod.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_validate_config(args) -> int:
    cfg = _load_config()
    enabled = [s for s in cfg.sources if s.enabled]
    print(f"config OK: {len(enabled)} enabled sources ({len(cfg.sources)} total)")
    if not args.check_sources:
        return 0
    failed = 0
    with make_client(cfg.http_timeout) as client:
        for src in enabled:
            try:
                n = len(fetch_source(src, client))
                print(f"  ok   {src.name}: {n} items")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {src.name}: {type(e).__name__}")
    return 1 if failed == len(enabled) else 0


def cmd_run(args) -> int:
    cfg = _load_config()
    dry_run = args.dry_run or config_mod.env_flag("LLMRSS_DRY_RUN")
    with make_client(cfg.http_timeout) as client:
        state = recover.ensure_state(cfg, client)
    print(f"state: {state}")
    store = Store(Path(cfg.state_dir) / "llmrss.db")
    try:
        return _run(cfg, store, dry_run, args)
    finally:
        store.close()


def _run(cfg, store: Store, dry_run: bool, args) -> int:
    with make_client(cfg.http_timeout) as client:
        items, errors = fetch_all(cfg.sources, client)
    res = store.ingest(items)
    pruned = store.prune(cfg.retention_days)
    print(f"fetched {len(items)} | new {len(res.new)} | duplicates {res.duplicates} "
          f"(cross-source {res.cross_source}) | pruned {pruned} | source errors {len(errors)}")
    enabled = sum(1 for s in cfg.sources if s.enabled)
    if enabled and len(errors) >= enabled:
        print("every source failed", file=sys.stderr)
        return 1

    if args.no_llm:
        if args.verbose:
            for iid in res.new[: args.show]:
                it = store.item(iid)
                print(f"  [{it['source']}] {it['title']}")
        if not dry_run:
            store.log_run(items_fetched=len(items), new=len(res.new), llm_calls=0, errors=errors)
        return 0

    llm = Claude(max_calls=cfg.batching.max_llm_calls_per_run)
    kept = n_scored = 0
    n_pref = 0
    status = 0
    try:
        n_pref, kept = prefilter.run(store, cfg, llm)
        print(f"prefilter: {n_pref} processed, {kept} kept")
        n_scored = score.run(store, cfg, llm)
        print(f"scored: {n_scored}")
    except RateLimited as e:
        # Not a failure: what's pending is processed on the next run.
        print(f"stopping early: {e}; pending items wait for the next run")
        errors.append(str(e))
    except BudgetExhausted as e:
        print(f"stopping early: {e}")
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        errors.append("auth")
        status = 1
    except LLMError as e:
        print(f"LLM error: {e}; pending items wait for the next run", file=sys.stderr)
        errors.append(str(e))

    days = llm.days_to_expiry()
    if days is not None and days < 14:
        print(f"WARNING: CLAUDE_CODE_OAUTH_TOKEN expires in {days:.0f} days: renew it")

    picked = selection.choose(store, cfg)
    if dry_run:
        render.write_all(store, cfg, preview=picked)
        print(f"dry-run: would publish {len(picked)} (preview written to {cfg.public_dir}/)")
        for iid in picked:
            r = store.db.execute("SELECT i.title, s.score FROM items i JOIN scores s "
                                 "ON s.item_id=i.id WHERE i.id=?", (iid,)).fetchone()
            print(f"  {r['score']:5.2f}  {r['title']}")
        return status

    selection.publish(store, picked)
    store.log_run(items_fetched=len(items), new=len(res.new), prefiltered=n_pref,
                  scored=n_scored, published=len(picked), llm_calls=llm.calls, errors=errors)
    render.write_all(store, cfg)
    print(f"published {len(picked)} | llm calls {llm.calls}")
    return status


def cmd_rebuild_feed(args) -> int:
    cfg = _load_config()
    store = Store(Path(cfg.state_dir) / "llmrss.db")
    try:
        out = render.write_all(store, cfg)
    finally:
        store.close()
    print(f"rebuilt {out}/")
    return 0


def cmd_weekly(args) -> int:
    cfg = _load_config()
    dry_run = args.dry_run or config_mod.env_flag("LLMRSS_DRY_RUN")
    store = Store(Path(cfg.state_dir) / "llmrss.db")
    llm = Claude(max_calls=3)
    try:
        week = trends.run(store, cfg, llm, force=args.force, dry_run=dry_run)
        if week is None:
            print("weekly: not due")
            return 0
        if not dry_run:
            render.write_all(store, cfg)
        print(f"weekly: {week}{' (dry-run)' if dry_run else ''}")
        return 0
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except LLMError as e:
        # Not fatal: it is retried on the next run since it is still due.
        print(f"weekly skipped: {e}")
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="llmrss")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate-config", help="validate LLMRSS_CONFIG")
    v.add_argument("--check-sources", action="store_true", help="also fetch every source")
    v.set_defaults(func=cmd_validate_config)

    r = sub.add_parser("run", help="daily run")
    r.add_argument("--dry-run", action="store_true", help="don't touch the published feed")
    r.add_argument("--no-llm", action="store_true", help="fetch + dedup only")
    r.add_argument("--show", type=int, default=30, help="new items to list with -v")
    r.set_defaults(func=cmd_run)

    sub.add_parser("rebuild-feed", help="regenerate public/ from the database"
                   ).set_defaults(func=cmd_rebuild_feed)
    w = sub.add_parser("weekly", help="weekly trends (no-op unless 7 days since the last)")
    w.add_argument("--force", action="store_true")
    w.add_argument("--dry-run", action="store_true")
    w.set_defaults(func=cmd_weekly)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
