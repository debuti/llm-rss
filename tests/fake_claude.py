#!/usr/bin/env python3
"""Fake `claude -p`. Behaviour comes from FAKE_CLAUDE_SCRIPT: a JSON list of responses
consumed one per call (state in FAKE_CLAUDE_STATE). Each response:
  {"result": "..."}                     -> success
  {"error": "rate"|"auth"|"other"}      -> is_error output
  {"echo_score": true}                  -> scores every input item 9/9/9/1
  {"echo_keep": true}                   -> keeps everything
"""
import json, os, sys

prompt = sys.stdin.read()
state = os.environ["FAKE_CLAUDE_STATE"]
n = int(open(state).read()) if os.path.exists(state) else 0
open(state, "w").write(str(n + 1))
with open(state + ".args", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
script = json.loads(os.environ["FAKE_CLAUDE_SCRIPT"])
r = script[min(n, len(script) - 1)]
base = {"type": "result", "is_error": False, "credential_expires_at": "2099-01-01T00:00:00Z"}
if "error" in r:
    status = {"rate": 429, "auth": 401, "other": 500}[r["error"]]
    print(json.dumps(base | {"is_error": True, "api_error_status": status, "result": "API Error"}))
    sys.exit(1)
if r.get("echo_score") or r.get("echo_keep"):
    items = json.loads(prompt.split("\n\nReturn ONLY")[0])
    if r.get("echo_keep"):
        res = {"drop": []}
    else:
        res = [{"i": it["i"], "novelty": 9, "impact": 9, "evidence": 9, "hype": 1,
                "category": "Hardware", "tags": ["RISC V", "open-hardware"], "why": "Because.",
                "plain": "Simply put.", "glossary": [{"term": "RISC-V", "meaning": "An open CPU design."}]}
               for it in items]
    r = {"result": json.dumps(res)}
print(json.dumps(base | {"result": r["result"]}))
