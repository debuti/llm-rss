import pytest

from llmrss.config import ConfigError, load

BASE = """
sources:
  - {name: a, type: rss, url: "https://x.test/feed"}
"""


def test_defaults():
    cfg = load({"LLMRSS_CONFIG": BASE})
    assert cfg.selection.threshold == 5
    assert cfg.models.score.startswith("claude-")
    assert cfg.sources[0].prefilter is True


def test_json():
    cfg = load({"LLMRSS_CONFIG": '{"sources":[{"name":"h","type":"hn","list":"show"}]}'})
    assert cfg.sources[0].list == "show"


def test_nested_overrides():
    cfg = load({"LLMRSS_CONFIG": BASE, "LLMRSS__SELECTION__THRESHOLD": "8.5",
                "LLMRSS__MODELS__SCORE": "m", "LLMRSS__RETENTION_DAYS": "30",
                "LLMRSS_DRY_RUN": "true"})
    assert (cfg.selection.threshold, cfg.models.score, cfg.retention_days) == (8.5, "m", 30)


@pytest.mark.parametrize("text, msg", [
    ("", "empty"),
    ("a: [", "not valid"),
    ("- 1", "mapping"),
    ("sources: []", "sources"),
    (BASE + "bogus: 1", "bogus"),
    (BASE + "  - {name: a, type: hn}", "duplicate"),
    ("sources: [{name: a, type: nope}]", "sources"),
])
def test_invalid(text, msg):
    with pytest.raises(ConfigError, match=msg):
        load({"LLMRSS_CONFIG": text})


def test_errors_do_not_leak_values():
    secret = "my-secret-profile"
    with pytest.raises(ConfigError) as e:
        load({"LLMRSS_CONFIG": BASE + f"selection: {{threshold: {secret}}}"})
    assert secret not in str(e.value)


def test_missing():
    with pytest.raises(ConfigError, match="not set"):
        load({})
