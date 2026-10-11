"""Spend limit for Claude API calls (decision D49).

Every model call the service makes is costed into a ledger table, and nothing is sent once the
next call could take the total past SPEND_LIMIT_USD. The limit covers all clinics together: it is
the operator's API credit, not a per-customer quota.

How it stays under the limit rather than noticing afterwards:
- Before a call, a worst-case cost is *reserved*: the prompt's length at a conservative 3
  characters per token, all of it at the full input rate (no cache discount), plus max_tokens of
  output. If the reservation does not fit, the call is refused. After the call the reservation is
  replaced by the real cost from the token counts the API reports.
- A Message Batches submission reserves the worst case of every request in it (at the batch
  discount) before it is sent, and is settled when its results are collected. A worker that dies
  after submitting keeps the reservation: unknown spend is counted, never forgotten.
- A reservation that is never settled (a crash mid-call) also stays counted. `admin spend` shows
  open reservations; `admin release-reservations` clears them once the bill is checked.

Within one process, check-and-reserve is under a lock. Across processes (API and worker) two
reservations can race; the overshoot is bounded by one call's worst case (about $0.17 for
Sonnet 5.5 at 16,000 max tokens), which is why the default warning fires at 80%.

Rates are US dollars per million tokens, from Anthropic's published pricing (October 2026).
Cache writes (5-minute TTL) cost 1.25x input; the Batches API halves every token.
"""

import logging
import math
import threading

import sqlalchemy as sa

from safety_eval.llm import ModelCallError

from .db import spend_ledger, utcnow

log = logging.getLogger("safety_eval.service")

# model: (input, output, cache read)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),   # prompts up to 100K tokens; ours are about 5K
}
CACHE_WRITE = 1.25
BATCH_DISCOUNT = 0.5
CHARS_PER_TOKEN = 3          # conservative: English is nearer 4, so estimates run high
WARN_AT = 0.8


class BudgetExceeded(ModelCallError):
    """The next call could pass the spend limit. A ModelCallError, so if it ever reaches the judge
    the conversation becomes needs_review, never pass."""


def rates(model):
    """Known rates; an unknown model (for example a refusal fallback) is costed at the dearest."""
    if model in PRICES:
        return PRICES[model]
    return max(PRICES.values())


def check_model(model):
    if model not in PRICES:
        raise ValueError(f"no price for model {model!r}: add it to budget.PRICES before using it")


def usage_cost(model, usage, batch=False):
    """US dollars for one call's reported token counts."""
    inp, out, read = rates(model)
    u = usage or {}
    dollars = (u.get("input_tokens", 0) * inp
               + u.get("cache_creation_input_tokens", 0) * inp * CACHE_WRITE
               + u.get("cache_read_input_tokens", 0) * read
               + u.get("output_tokens", 0) * out) / 1e6
    return dollars * (BATCH_DISCOUNT if batch else 1.0)


def worst_case(model, n_chars, max_tokens, batch=False):
    inp, out, _ = rates(model)
    dollars = (math.ceil(n_chars / CHARS_PER_TOKEN) * inp + max_tokens * out) / 1e6
    return dollars * (BATCH_DISCOUNT if batch else 1.0)


class Budget:
    def __init__(self, engine, limit_usd):
        if limit_usd is None or limit_usd <= 0:
            raise ValueError("the spend limit must be a positive number of US dollars")
        self.engine, self.limit = engine, float(limit_usd)
        self._lock = threading.Lock()
        self._warned = False

    def spent(self):
        """Settled costs plus open reservations."""
        with self.engine.connect() as conn:
            return float(conn.execute(sa.select(sa.func.coalesce(sa.func.sum(spend_ledger.c.usd), 0))).scalar())

    def summary(self):
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(
                sa.func.coalesce(sa.func.sum(sa.case((spend_ledger.c.settled, spend_ledger.c.usd), else_=0)), 0),
                sa.func.coalesce(sa.func.sum(sa.case((spend_ledger.c.settled, 0), else_=spend_ledger.c.usd)), 0),
                sa.func.count().filter(~spend_ledger.c.settled))).one()
        settled, reserved, n_open = float(row[0]), float(row[1]), int(row[2])
        return {"limit_usd": self.limit, "settled_usd": round(settled, 4), "reserved_usd": round(reserved, 4),
                "open_reservations": n_open, "remaining_usd": round(self.limit - settled - reserved, 4)}

    def has_room(self, usd):
        return self.spent() + usd <= self.limit

    def reserve(self, usd, kind, model, ref=None):
        """Ledger id of a new reservation, or raise BudgetExceeded."""
        with self._lock:
            spent = self.spent()
            if spent + usd > self.limit:
                log.warning("spend limit reached: %.2f of %.2f USD used, next %s needs up to %.2f",
                            spent, self.limit, kind, usd)
                raise BudgetExceeded("spend limit reached")
            with self.engine.begin() as conn:
                ledger_id = conn.execute(spend_ledger.insert().values(
                    created_at=utcnow(), kind=kind, model=model, ref=ref, usd=usd, settled=False)
                    .returning(spend_ledger.c.id)).scalar_one()
        self._maybe_warn(spent + usd)
        return ledger_id

    def settle(self, ledger_id, usd, model=None, usage=None, ref=None):
        values = {"usd": usd, "settled": True, "settled_at": utcnow()}
        values.update({k: v for k, v in (("model", model), ("ref", ref)) if v is not None})
        for field in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            if usage and field in usage:
                values[field] = int(usage[field])
        with self.engine.begin() as conn:
            conn.execute(spend_ledger.update().where(spend_ledger.c.id == ledger_id).values(**values))

    def set_ref(self, ledger_id, ref):
        with self.engine.begin() as conn:
            conn.execute(spend_ledger.update().where(spend_ledger.c.id == ledger_id).values(ref=ref))

    def find_open(self, ref):
        with self.engine.connect() as conn:
            return conn.execute(sa.select(spend_ledger.c.id).where(
                spend_ledger.c.ref == ref, ~spend_ledger.c.settled)).scalar()

    def release_open(self):
        """Drop open reservations (after checking the real bill). Returns how many."""
        with self.engine.begin() as conn:
            return conn.execute(spend_ledger.delete().where(~spend_ledger.c.settled)).rowcount

    def _maybe_warn(self, total):
        if not self._warned and total >= WARN_AT * self.limit:
            self._warned = True
            log.warning("spend is at %.0f%% of the %.2f USD limit", 100 * total / self.limit, self.limit)


class BudgetedBackend:
    """Wraps the live API backend: every call reserves its worst case first and settles to the
    real cost after. A refused or failed call settles to what was actually reported (zero if
    nothing), so the ledger matches the bill. Other attributes (client, model, max_tokens, effort)
    pass through, so the batch worker can use it like the backend it wraps."""

    def __init__(self, backend, budget):
        self.backend, self.budget = backend, budget

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def complete(self, system, prompt, cache_prefix=None):
        model = self.backend.model
        ledger_id = self.budget.reserve(
            worst_case(model, len(system) + len(prompt), self.backend.max_tokens), "call", model)
        try:
            resp = self.backend.complete(system, prompt, cache_prefix=cache_prefix)
        except ModelCallError as exc:
            self._settle_failure(ledger_id, exc, model)
            raise
        usage = (resp.raw or {}).get("usage") or {}
        self.budget.settle(ledger_id, usage_cost(resp.model, usage), model=resp.model, usage=usage)
        return resp

    def _settle_failure(self, ledger_id, exc, model):
        """A refusal or max_tokens stop is billed: settle to the counts the API reported. An HTTP
        error response (it has a status code) is not billed: settle to zero. Anything else (a
        timeout, a dropped connection) may or may not have been billed, and the API cannot say:
        leave the worst-case reservation open, because over-counting is the safe direction."""
        usage = getattr(exc, "usage", None)
        if usage is not None:
            served = getattr(exc, "model", None) or model
            self.budget.settle(ledger_id, usage_cost(served, usage), model=served, usage=usage)
        elif getattr(exc.__cause__, "status_code", None) is not None:
            self.budget.settle(ledger_id, 0.0)


def conversation_worst_case(model, calls, max_tokens, batch=False):
    """Worst case for a conversation's planned first-attempt calls, plus one retry each (the judge
    retries once on malformed output)."""
    return 2 * sum(worst_case(model, len(system) + len(text), max_tokens, batch)
                   for _label, system, text, _prefix in calls)
