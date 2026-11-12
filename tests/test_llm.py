import pytest

from llmrss.llm import AuthError, BadOutput, BudgetExhausted, RateLimited, extract_json


def test_ok_and_flags(fake_claude):
    llm = fake_claude([{"result": "hello"}])
    assert llm.call("m", "sys", "user") == "hello"
    args = fake_claude.args()[0]
    assert args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert llm.days_to_expiry() > 1000


@pytest.mark.parametrize("kind, exc", [("rate", RateLimited), ("auth", AuthError)])
def test_errors(fake_claude, kind, exc):
    with pytest.raises(exc):
        fake_claude([{"error": kind}]).call("m", "s", "u")


def test_budget(fake_claude):
    llm = fake_claude([{"result": "x"}], max_calls=1)
    llm.call("m", "s", "u")
    with pytest.raises(BudgetExhausted):
        llm.call("m", "s", "u")


def test_json_retry(fake_claude):
    llm = fake_claude([{"result": "sorry, no"}, {"result": '```json\n{"a": 1}\n```'}])
    assert llm.call_json("m", "s", "u", lambda d: d) == {"a": 1}
    assert fake_claude.calls() == 2


def test_json_gives_up(fake_claude):
    with pytest.raises(BadOutput):
        fake_claude([{"result": "nope"}]).call_json("m", "s", "u", lambda d: d)


def test_extract_json():
    assert extract_json('Here: [{"i": 1}] done') == [{"i": 1}]
    with pytest.raises(ValueError):
        extract_json("nothing")
