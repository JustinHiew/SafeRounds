"""The model-call layer and cache, tested with fakes: no model access needed."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from safety_eval.cache import ResponseCache, cache_key, confirm_batch
from safety_eval.llm import CliBackend, FakeBackend, ModelCallError


def fake_run(stdout="", returncode=0, seen=None):
    def runner(cmd, input, capture_output, text, cwd, timeout):
        if seen is not None:
            seen.update(cmd=cmd, cwd=cwd, cwd_files=os.listdir(cwd), input=input)
        return SimpleNamespace(stdout=stdout, stderr="boom", returncode=returncode)
    return runner


OK = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hello",
                 "modelUsage": {"claude-haiku-5-5": {}, "claude-sonnet-5-5": {}}})


def test_cli_disables_tools_and_runs_in_empty_dir():
    seen = {}
    resp = CliBackend(model="sonnet", runner=fake_run(OK, seen=seen)).complete("sys", "prompt")
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--safe-mode" in cmd and "--strict-mcp-config" in cmd
    assert seen["cwd_files"] == []                     # empty working directory
    assert seen["input"] == "prompt"
    # The helper model in modelUsage is ignored; the requested family is recorded.
    assert resp.text == "hello" and resp.model == "claude-sonnet-5-5"


@pytest.mark.parametrize("runner", [
    fake_run(returncode=1),
    fake_run(stdout="not json"),
    fake_run(stdout=json.dumps({"subtype": "error_max_turns", "is_error": True})),
])
def test_cli_failures_raise(runner):
    with pytest.raises(ModelCallError):
        CliBackend(runner=runner).complete("s", "p")


def test_cli_timeout_raises():
    def runner(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)
    with pytest.raises(ModelCallError):
        CliBackend(runner=runner).complete("s", "p")


def test_fake_backend_queues_replies_and_errors():
    fake = FakeBackend(["a", ModelCallError("x"), "c"])
    assert fake.complete("s", "p").text == "a"
    with pytest.raises(ModelCallError):
        fake.complete("s", "p")
    assert fake.complete("s", "p").text == "c"
    assert len(fake.calls) == 3


def test_cache_round_trip_and_key_order(tmp_path):
    assert cache_key(a=1, b=2) == cache_key(b=2, a=1)
    assert cache_key(a=1) != cache_key(a=2)
    cache = ResponseCache("x", root=tmp_path)
    key = cache_key(model="m", conversation_id="c1")
    assert cache.get(key) is None and key not in cache
    cache.put(key, {"text": "hi"})
    assert cache.get(key) == {"text": "hi"} and key in cache


def test_confirm_batch_asks_only_above_threshold():
    asked = []
    assert confirm_batch(100, ask=lambda q: asked.append(q) or "n")
    assert not asked
    assert not confirm_batch(101, ask=lambda q: "n")
    assert confirm_batch(101, ask=lambda q: "y")
    assert confirm_batch(500, assume_yes=True)
