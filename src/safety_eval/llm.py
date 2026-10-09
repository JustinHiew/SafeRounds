"""One interface for calling a model, with interchangeable backends.

- CliBackend: the Claude Code command line (`claude -p`) on the user's subscription. Used for real.
- FakeBackend: returns canned text. Used by tests, so they never need model access.
- ApiBackend: the Anthropic Python SDK. Written and unit-tested with a fake client, never called
  for real in this project (the brief allows no paid API use).

Every response records the exact model name the backend reports, not just the alias asked for.
"""

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass


@dataclass
class ModelResponse:
    text: str
    model: str            # exact model name, e.g. "claude-sonnet-5-5"
    latency_s: float
    raw: dict             # backend metadata kept for the cache (never shown to the judge)


class ModelCallError(RuntimeError):
    """Timeout, crash or error result. Callers treat this as 'needs human review', never as a pass."""


# Flags checked against `claude --help` (Claude Code 2.1.293); see docs/decisions.md D6.
CLI_FLAGS = [
    "-p",
    "--tools", "",                 # no built-in tools: cannot read files or run commands
    "--safe-mode",                 # no CLAUDE.md, skills, plugins, hooks or MCP servers
    "--strict-mcp-config",         # and no MCP servers from any other config
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format", "json",
]


class CliBackend:
    def __init__(self, model="sonnet", timeout_s=600, runner=subprocess.run):
        self.model = model
        self.timeout_s = timeout_s
        self.runner = runner      # injectable so the command can be tested without running it

    def command(self, system):
        return ["claude", *CLI_FLAGS, "--model", self.model, "--system-prompt", system]

    def complete(self, system, prompt):
        start = time.monotonic()
        # A fresh empty directory per call: even if a tool slipped through, there is nothing to read.
        with tempfile.TemporaryDirectory(prefix="judge-") as empty_dir:
            try:
                proc = self.runner(self.command(system), input=prompt, capture_output=True,
                                   text=True, cwd=empty_dir, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise ModelCallError(f"timed out after {self.timeout_s}s") from exc
        if proc.returncode != 0:
            raise ModelCallError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ModelCallError("claude did not return JSON") from exc
        if out.get("is_error") or out.get("subtype") != "success":
            raise ModelCallError(f"claude returned an error result: {out.get('subtype')}")
        return ModelResponse(text=out["result"], model=self.served_model(out),
                             latency_s=time.monotonic() - start,
                             raw={k: out.get(k) for k in ("modelUsage", "usage", "stop_reason")})

    def served_model(self, out):
        """The model that wrote the reply. modelUsage can also list small internal helper calls,
        so prefer the entry whose name contains the alias asked for."""
        names = list(out.get("modelUsage") or {})
        matching = [n for n in names if self.model in n]
        if matching:
            return matching[0]
        if names:
            return names[0]
        return self.model


class ApiBackend:
    """Same interface over the official Anthropic SDK. The SDK is imported only when no client is
    passed in, so tests (and anyone without the package) never need it."""

    def __init__(self, model="claude-opus-5-5", max_tokens=16000, client=None):
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            import anthropic   # optional dependency, deliberately not in pyproject.toml
            client = anthropic.Anthropic()
        self.client = client

    def complete(self, system, prompt):
        start = time.monotonic()
        try:
            msg = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                              system=system,
                                              messages=[{"role": "user", "content": prompt}])
        except Exception as exc:   # any SDK error is a failed call: the judge fails closed
            raise ModelCallError(f"API call failed: {exc}") from exc
        if msg.stop_reason in ("refusal", "max_tokens"):
            raise ModelCallError(f"API stopped with {msg.stop_reason}")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return ModelResponse(text=text, model=msg.model, latency_s=time.monotonic() - start,
                             raw={"stop_reason": msg.stop_reason})


class FakeBackend:
    """Returns queued replies in order (or one fixed reply). Records every call for assertions."""

    def __init__(self, replies, model="fake-model"):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self.model = model
        self.calls = []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(text=reply, model=self.model, latency_s=0.0, raw={})
