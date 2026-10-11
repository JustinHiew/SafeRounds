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

    def complete(self, system, prompt, cache_prefix=None):
        # cache_prefix is an API prompt-caching hint; the command line has no use for it.
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


FALLBACK_BETA = "server-side-fallback-2026-07-01"   # the `fallbacks: "default"` scalar form
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def build_params(model, max_tokens, system, prompt, cache_prefix=None, effort=None):
    """Request parameters shared by ApiBackend and the Batches API, so both send the same thing.

    The text the model receives is exactly `system` and `prompt`. Prompt caching only changes how
    it is cut into blocks: `system` is one block and the user message is the stable prefix then the
    rest, each cacheable up to a breakpoint (caching is a prefix match over system then messages)."""
    if cache_prefix and not prompt.startswith(cache_prefix):
        raise ValueError("prompt must start with cache_prefix")
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if cache_prefix and cache_prefix != prompt:
        content = [{"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": prompt[len(cache_prefix):]}]
    elif cache_prefix:      # nothing varies: the whole prompt is the prefix (empty blocks are rejected)
        content = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
    else:
        content = [{"type": "text", "text": prompt}]
    params = dict(model=model, max_tokens=max_tokens, system=system_blocks,
                  messages=[{"role": "user", "content": content}])
    if effort:
        params["output_config"] = {"effort": effort}
    return params


def message_text(msg):
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def message_usage(msg):
    """Token counts only (numbers, never text)."""
    usage = getattr(msg, "usage", None)
    return {f: int(getattr(usage, f, 0) or 0) for f in USAGE_FIELDS}


class ApiBackend:
    """Same interface over the official Anthropic SDK. The SDK is imported only when no client is
    passed in, so tests (and anyone without the package) never need it.

    Prompt caching: `system` and the stable `cache_prefix` of the prompt carry ephemeral
    cache_control, so repeated calls pay the cheap cache-read rate for them. `fallbacks=True` adds
    the server-side refusal fallback (`fallbacks: "default"`, beta header FALLBACK_BETA, sent with
    client.beta.messages.create); the served model is msg.model, so a fallback shows in the result.
    A final stop_reason of refusal still raises, so it fails closed. Not for Haiku 5.5, which has
    no server-side fallback."""

    def __init__(self, model="claude-opus-5-5", max_tokens=16000, client=None, effort=None,
                 fallbacks=False):
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.fallbacks = fallbacks and "haiku" not in model
        if client is None:
            import anthropic   # optional dependency, deliberately not in pyproject.toml
            client = anthropic.Anthropic()
        self.client = client

    def params(self, system, prompt, cache_prefix=None):
        return build_params(self.model, self.max_tokens, system, prompt, cache_prefix, self.effort)

    def complete(self, system, prompt, cache_prefix=None):
        start = time.monotonic()
        try:
            params = self.params(system, prompt, cache_prefix)
            if self.fallbacks:
                msg = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default",
                                                       **params)
            else:
                msg = self.client.messages.create(**params)
        except Exception as exc:   # any SDK error is a failed call: the judge fails closed
            raise ModelCallError(f"API call failed: {exc}") from exc
        if msg.stop_reason in ("refusal", "max_tokens"):
            raise ModelCallError(f"API stopped with {msg.stop_reason}")
        return ModelResponse(text=message_text(msg), model=msg.model,
                             latency_s=time.monotonic() - start,
                             raw={"stop_reason": msg.stop_reason, "usage": message_usage(msg)})


class FakeBackend:
    """Returns queued replies in order (or one fixed reply). Records every call for assertions."""

    def __init__(self, replies, model="fake-model"):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self.model = model
        self.calls = []
        self.prefixes = []   # the cache_prefix of each call, parallel to calls

    def complete(self, system, prompt, cache_prefix=None):
        self.calls.append((system, prompt))
        self.prefixes.append(cache_prefix)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(text=reply, model=self.model, latency_s=0.0, raw={})
