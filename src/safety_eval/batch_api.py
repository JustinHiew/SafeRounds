"""The Anthropic Message Batches API for the service's batch path (about half the price of
synchronous calls, asynchronous: most batches end within an hour, at most 24).

The pieces are deliberately small and free of judging logic:

- build_requests / submit / poll / collect talk to the API (the client is passed in, so tests use a
  fake and nothing here ever needs a key).
- ReplayBackend hands the judge the batch's answer for the exact prompt it asks about, and calls a
  live backend for anything else. The worker then runs the normal `judge()` with it, so JSON
  parsing, the one malformed-output retry, quote checks and fail-closed behaviour are the same code
  as the synchronous path rather than a copy of it.

No server-side refusal `fallbacks` here: the Batches API rejects the parameter. A refusal in a batch
becomes "errored", gets one live attempt (which has fallbacks if the live backend does), and if that
refuses too, the judge records needs_review.
"""

import hashlib

from .llm import ModelResponse, build_params, message_text, message_usage

NOT_SUCCEEDED = ("errored", "canceled", "expired")


def prompt_hash(system, prompt):
    """Identifies the exact text the model is asked about. Never stores or logs the text."""
    return hashlib.sha256((system + "\x00" + prompt).encode()).hexdigest()


def build_requests(items, model, max_tokens, effort=None):
    """items: iterable of (custom_id, system, prompt, cache_prefix). Same params as ApiBackend."""
    return [{"custom_id": custom_id,
             "params": build_params(model, max_tokens, system, prompt, cache_prefix, effort)}
            for custom_id, system, prompt, cache_prefix in items]


def submit(client, requests):
    """Create the batch; returns its id."""
    return client.messages.batches.create(requests=requests).id


def poll(client, batch_id):
    """processing_status: "in_progress", "canceling" or "ended"."""
    return client.messages.batches.retrieve(batch_id).processing_status


def collect(client, batch_id):
    """{custom_id: ("succeeded", text, model, usage) | (status, None, None, usage_or_None)}.

    Results arrive in any order, so they are keyed by custom_id. A succeeded result that stopped
    for refusal or max_tokens is not a usable answer and counts as "errored"."""
    out = {}
    for item in client.messages.batches.results(batch_id):
        kind = item.result.type
        if kind == "succeeded":
            msg = item.result.message
            if msg.stop_reason in ("refusal", "max_tokens"):
                out[item.custom_id] = ("errored", None, None, message_usage(msg))
            else:
                out[item.custom_id] = ("succeeded", message_text(msg), msg.model, message_usage(msg))
        else:
            out[item.custom_id] = (kind if kind in NOT_SUCCEEDED else "errored", None, None, None)
    return out


class ReplayBackend:
    """answers_by_prompt_hash: {hash: (text, model, usage)} from a collected batch.

    complete() returns the stored answer for that exact system+prompt the first time it is asked.
    Anything else goes to live_backend: the judge's retry after a malformed answer (same prompt, the
    answer is already used), and every item the batch did not answer (errored, canceled, expired,
    refused, or never submitted). Live errors propagate, so the judge fails closed."""

    def __init__(self, answers_by_prompt_hash, live_backend):
        self.answers = dict(answers_by_prompt_hash)
        self.live = live_backend
        self.model = live_backend.model
        self.replayed = 0

    def complete(self, system, prompt, cache_prefix=None):
        answer = self.answers.pop(prompt_hash(system, prompt), None)
        if answer is None:
            return self.live.complete(system, prompt, cache_prefix=cache_prefix)
        text, model, usage = answer
        self.replayed += 1
        return ModelResponse(text=text, model=model, latency_s=0.0,
                             raw={"usage": usage, "batch": True})
