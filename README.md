# llm-rss

Daily news filter powered by Claude. It reads many sources (RSS, Hacker News, GitHub trending),
discards the noise and publishes its own RSS feed with only what points to revolutionary ideas,
plus a weekly trends feed. Runs on GitHub Actions, keeps its SQLite state in the Actions cache
and is configured entirely through Actions variables. See [PLAN.md](PLAN.md) for the design.

## Setup (GitHub)

1. Public repo (free Actions minutes and Pages).
2. Settings → Pages → Source: **GitHub Actions**.
3. Locally: `claude setup-token`, then add it as the secret `CLAUDE_CODE_OAUTH_TOKEN`.
4. Add a repository **variable** `LLMRSS_CONFIG` with your config (start from
   `config.example.yaml`; set `feed.link` to your Pages URL).
5. Run the `llmrss` workflow once by hand with `dry_run` on, then off.

Feeds: `https://<user>.github.io/<repo>/feed.xml` and `.../weekly.xml`.

Override any key without editing the config: `LLMRSS__SELECTION__THRESHOLD=6`
(`__` separates levels).

## Local

```sh
uv sync
export LLMRSS_CONFIG="$(cat config.yaml)"
uv run llmrss validate-config --check-sources
uv run llmrss run --no-llm        # fetch + dedup only, no quota used
uv run llmrss run --dry-run       # full pipeline, writes a preview to public/, publishes nothing
uv run llmrss weekly --force --dry-run
uv run llmrss rebuild-feed
uv run pytest
```
