"""llmrss CLI: run | weekly | validate-config | rebuild-feed."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config as config_mod
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
    if not args.no_llm:
        print("LLM stage not implemented yet: use --no-llm", file=sys.stderr)
        return 2

    store = Store(Path(cfg.state_dir) / "llmrss.db")
    try:
        with make_client(cfg.http_timeout) as client:
            items, errors = fetch_all(cfg.sources, client)
        res = store.ingest(items)
        pruned = store.prune(cfg.retention_days)
        if not dry_run:
            store.log_run(items_fetched=len(items), new=len(res.new), llm_calls=0, errors=errors)
        print(f"fetched {len(items)} | new {len(res.new)} | duplicates {res.duplicates} "
              f"(cross-source {res.cross_source}) | pruned {pruned} | source errors {len(errors)}")
        if args.verbose:
            for iid in res.new[: args.show]:
                it = store.item(iid)
                print(f"  [{it['source']}] {it['title']}")
    finally:
        store.close()
    ok_sources = sum(1 for s in cfg.sources if s.enabled) - len(errors)
    return 1 if ok_sources <= 0 else 0


def cmd_not_yet(args) -> int:
    print(f"'{args.cmd}' not implemented yet", file=sys.stderr)
    return 2


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

    for name in ("weekly", "rebuild-feed"):
        sub.add_parser(name).set_defaults(func=cmd_not_yet)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
