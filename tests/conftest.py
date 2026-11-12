from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    return lambda name: (FIX / name).read_bytes()


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Returns a function(script) -> Claude wired to the fake CLI."""
    import json
    from llmrss.llm import Claude

    def make(script, max_calls=10):
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", json.dumps(script))
        monkeypatch.setenv("FAKE_CLAUDE_STATE", str(tmp_path / "calls"))
        return Claude(max_calls=max_calls, binary=str(Path(__file__).parent / "fake_claude.py"))

    make.calls = lambda: int((tmp_path / "calls").read_text())
    make.args = lambda: [json.loads(x) for x in (tmp_path / "calls.args").read_text().splitlines()]
    return make


@pytest.fixture
def cfg(tmp_path):
    from llmrss.config import load
    text = f"""
profile: "firmware"
state_dir: "{tmp_path}/state"
public_dir: "{tmp_path}/public"
selection: {{threshold: 7, max_per_day: 2}}
batching: {{prefilter_batch: 2, score_batch: 2}}
feed: {{link: "https://u.github.io/r"}}
sources:
  - {{name: a, type: rss, url: "https://a.test/"}}
  - {{name: b, type: rss, url: "https://b.test/", prefilter: false}}
"""
    return load({"LLMRSS_CONFIG": text})
