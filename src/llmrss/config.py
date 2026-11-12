"""Load config from LLMRSS_CONFIG (YAML/JSON) plus LLMRSS__A__B=value overrides.

Never log the config or the profile: Actions logs are public.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

RESERVED = {"LLMRSS_CONFIG", "LLMRSS_DRY_RUN", "LLMRSS_CACHE_HIT"}
OVERRIDE_PREFIX = "LLMRSS__"


class ConfigError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Models(_Strict):
    prefilter: str = "claude-haiku-5-5"
    score: str = "claude-opus-5-5"
    weekly: str = "claude-opus-5-5"


class Selection(_Strict):
    threshold: float = Field(7.0, ge=0, le=10)
    max_per_day: int = Field(10, ge=0)
    min_per_day: int = Field(0, ge=0)


class Batching(_Strict):
    prefilter_batch: int = Field(80, ge=1)
    score_batch: int = Field(15, ge=1)
    max_llm_calls_per_run: int = Field(40, ge=0)


class Scoring(_Strict):
    novelty: float = 0.35
    impact: float = 0.35
    evidence: float = 0.30
    hype: float = 0.25          # subtracted
    per_extra_source: float = 0.5


class Feed(_Strict):
    title: str = "LLM-RSS"
    link: str = "https://example.github.io/llm-rss/"
    description: str = "What matters, without the noise"
    keep_entries: int = Field(300, ge=1)


class _SourceBase(_Strict):
    name: str
    prefilter: bool = True
    weight: float = Field(1.0, gt=0)
    enabled: bool = True


class RssSource(_SourceBase):
    type: Literal["rss"]
    url: str


class HnSource(_SourceBase):
    type: Literal["hn"]
    list: Literal["front_page", "show", "ask", "story"] = "front_page"
    min_points: int = 0
    hits: int = Field(50, ge=1, le=1000)


class GithubTrendingSource(_SourceBase):
    type: Literal["github_trending"]
    language: str = ""
    since: Literal["daily", "weekly", "monthly"] = "daily"


Source = Annotated[Union[RssSource, HnSource, GithubTrendingSource], Field(discriminator="type")]


class Config(_Strict):
    profile: str = ""
    models: Models = Models()
    selection: Selection = Selection()
    batching: Batching = Batching()
    scoring: Scoring = Scoring()
    feed: Feed = Feed()
    retention_days: int = Field(365, ge=1)
    sources: list[Source] = Field(min_length=1)
    rubric: str | None = None
    examples: dict[str, list[str]] = {}        # {"good": [...], "bad": [...]}
    tag_aliases: dict[str, str] = {}
    state_dir: str = "state"
    public_dir: str = "public"
    http_timeout: float = 20.0


def _parse_scalar(v: str) -> Any:
    """Parse an override value as YAML so numbers/bools/lists work; fall back to str."""
    try:
        return yaml.safe_load(v)
    except yaml.YAMLError:
        return v


def apply_overrides(data: dict, env: dict[str, str]) -> dict:
    for key, raw in env.items():
        if not key.startswith(OVERRIDE_PREFIX):
            continue
        path = [p.lower() for p in key[len(OVERRIDE_PREFIX):].split("__") if p]
        if not path:
            continue
        node = data
        for p in path[:-1]:
            if not isinstance(node.get(p), dict):
                node[p] = {}
            node = node[p]
        node[path[-1]] = _parse_scalar(raw)
    return data


def parse_text(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ConfigError("LLMRSS_CONFIG is empty")
    try:
        data = json.loads(text) if text[0] in "{[" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        # Don't echo the content: only the position.
        raise ConfigError(f"config is not valid YAML/JSON ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping at the top level")
    return data


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    text = env.get("LLMRSS_CONFIG")
    if text is None:
        raise ConfigError("LLMRSS_CONFIG is not set")
    data = apply_overrides(parse_text(text), env)
    try:
        cfg = Config.model_validate(data)
    except ValidationError as e:
        # Report locations and messages only, never input values.
        msgs = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        raise ConfigError("invalid config:\n  " + "\n  ".join(msgs)) from None
    names = [s.name for s in cfg.sources]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ConfigError(f"duplicate source names: {sorted(dup)}")
    return cfg


def env_flag(name: str, env: dict[str, str] | None = None) -> bool:
    v = (os.environ if env is None else env).get(name, "")
    return v.strip().lower() in {"1", "true", "yes", "on"}
