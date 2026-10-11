"""Batch worker: a simple database-backed queue (a batch's conversations with no result yet).

Run one worker (`python -m safety_eval.service.worker`). A second would be stopped from double
recording by the unique key on judge_results, but would waste model calls; real queue claiming
(SELECT ... FOR UPDATE SKIP LOCKED on Postgres) is a later hardening step (docs/service.md).
"""

import argparse
import logging
import time

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from safety_eval import batch_api
from safety_eval.clinic import Clinic
from safety_eval.judge import planned_calls
from safety_eval.schemas import BaseConversation

from .budget import Budget, BudgetedBackend, BudgetExceeded, worst_case
from .db import batches, conversations, insert_result, judge_results, make_engine, utcnow
from .judging import judge_safely, load_prompt, worst_case_usd
from .settings import Settings, production_backend

log = logging.getLogger("safety_eval.service")


def _budget(backend):
    return backend.budget if isinstance(backend, BudgetedBackend) else None


def _affordable(backend, row, batch=False):
    """False when the spend limit could be passed: the conversation then stays queued (D49)."""
    budget = _budget(backend)
    if budget is None:
        return True
    try:
        usd = worst_case_usd(row.deidentified, row.clinic_id, row.prompt_version, backend, batch)
    except Exception:
        return True      # cannot plan it: judge_safely records needs_review without a model call
    if budget.has_room(usd):
        return True
    log.warning("spend limit reached: conversation %s stays queued", row.pk)
    return False


def process_pending(engine, backend, limit=10):
    """Judge up to `limit` queued conversations. Returns how many were processed. Stops early,
    leaving the rest queued, when the spend limit could be passed."""
    with engine.connect() as conn:
        pending = conn.execute(
            sa.select(conversations.c.pk, conversations.c.clinic_id, conversations.c.batch_id,
                      conversations.c.deidentified, batches.c.prompt_version)
            .join(batches, batches.c.id == conversations.c.batch_id)
            .where(~sa.exists().where(judge_results.c.conversation_pk == conversations.c.pk))
            .order_by(conversations.c.pk).limit(limit)).all()
    done = 0
    for row in pending:
        if not _affordable(backend, row):
            break
        done += 1
        with engine.begin() as conn:    # mark running before the (slow) model call
            conn.execute(batches.update().where(batches.c.id == row.batch_id,
                                                batches.c.status == "queued").values(status="running"))
        usage = {}
        result = judge_safely(row.deidentified, row.clinic_id, row.prompt_version, backend, usage)
        record_result(engine, row, result, usage)
    return done


def record_result(engine, row, result, usage):
    try:
        with engine.begin() as conn:
            insert_result(conn, row.clinic_id, row.pk, result, usage)
            finish_if_complete(conn, row.batch_id)
    except IntegrityError:
        log.warning("result for conversation %s was already recorded", row.pk)


# ------------------------------------------------------------------ Message Batches API path
# State lives in the database, so a restarted worker carries on: batches.anthropic_batch_id says
# "submitted, waiting"; conversations with no result are still to be judged. Nothing is kept in
# memory between passes, and prompts are re-rendered (deterministically) when results come back
# rather than stored.

def _pending_rows(conn, batch_id):
    return conn.execute(
        sa.select(conversations.c.pk, conversations.c.clinic_id, conversations.c.batch_id,
                  conversations.c.deidentified, batches.c.prompt_version)
        .join(batches, batches.c.id == conversations.c.batch_id)
        .where(conversations.c.batch_id == batch_id,
               ~sa.exists().where(judge_results.c.conversation_pk == conversations.c.pk))
        .order_by(conversations.c.pk)).all()


def _custom_id(pk, label):
    return f"c{pk}-{label}"        # letters, digits, - and _ only; well under the 64 character limit


def _plan(row):
    """[(custom_id, system, prompt, cache_prefix)] for a conversation's first-attempt calls."""
    conv = BaseConversation.model_validate(row.deidentified)
    calls = planned_calls(load_prompt(row.prompt_version), conv, clinic=Clinic.load(row.clinic_id))
    return [(_custom_id(row.pk, label), system, text, prefix) for label, system, text, prefix in calls]


def _plan_safely(row):
    try:
        return _plan(row)
    except Exception as exc:       # judge_safely will record needs_review for it later
        log.error("could not plan conversation %s for the batch API (%s)", row.pk, type(exc).__name__)
        return []


def _judge_live(engine, rows, backend):
    for row in rows:
        usage = {}
        result = judge_safely(row.deidentified, row.clinic_id, row.prompt_version, backend, usage)
        record_result(engine, row, result, usage)


def _submit_new_batches(engine, backend, client):
    with engine.connect() as conn:
        todo = conn.execute(
            sa.select(batches.c.id).where(
                batches.c.anthropic_batch_id.is_(None), batches.c.status.in_(("queued", "running")),
                sa.exists().where(conversations.c.batch_id == batches.c.id,
                                  ~sa.exists().where(judge_results.c.conversation_pk == conversations.c.pk)))
            .order_by(batches.c.created_at)).scalars().all()
    budget = _budget(backend)
    max_tokens = getattr(backend, "max_tokens", 16000)
    for batch_id in todo:
        with engine.connect() as conn:
            rows = _pending_rows(conn, batch_id)
        planned = [(row, _plan_safely(row)) for row in rows]
        unplannable = [row for row, items in planned if not items]
        if unplannable:
            _judge_live(engine, unplannable, backend)   # judge_safely records needs_review, no model call
        items, reserve = [], 0.0
        room = budget.limit - budget.spent() if budget else float("inf")
        for row, row_items in planned:
            # Send only what fits the budget at worst case; the rest goes in a later pass, once
            # this chunk is settled at its real (much lower) cost.
            cost = sum(worst_case(backend.model, len(s) + len(p), max_tokens, batch=True)
                       for _cid, s, p, _pre in row_items)
            if not row_items or reserve + cost > room:
                continue
            items += row_items
            reserve += cost
        if not items:
            if any(row_items for _row, row_items in planned):
                log.warning("batch %s: spend limit reached; it stays queued", batch_id)
            continue
        try:
            ledger_id = budget.reserve(reserve, "batch", backend.model, ref=f"pending:{batch_id}") if budget else None
        except BudgetExceeded:       # another process reserved first: wait for the next pass
            log.warning("batch %s: spend limit reached; it stays queued", batch_id)
            continue
        try:
            requests = batch_api.build_requests(items, backend.model, max_tokens,
                                                getattr(backend, "effort", None))
            remote_id = batch_api.submit(client, requests)
        except Exception as exc:
            # Left queued for the next pass rather than judged live at twice the price.
            log.error("batch %s: submit to the Message Batches API failed (%s); will retry",
                      batch_id, type(exc).__name__)
            if ledger_id is not None and getattr(exc, "status_code", None) is not None:
                budget.settle(ledger_id, 0.0)     # an HTTP error response: nothing was created or billed
            continue
        if ledger_id is not None:
            budget.set_ref(ledger_id, remote_id)
        with engine.begin() as conn:
            conn.execute(batches.update().where(batches.c.id == batch_id).values(
                status="running", anthropic_batch_id=remote_id))
        log.info("batch %s submitted to the Message Batches API: %d requests", batch_id, len(items))


def _collect_ended_batches(engine, backend, client):
    with engine.connect() as conn:
        waiting = conn.execute(
            sa.select(batches.c.id, batches.c.anthropic_batch_id).where(
                batches.c.anthropic_batch_id.is_not(None), batches.c.status != "done")
            .order_by(batches.c.created_at)).all()
    judged = 0
    for batch_id, remote_id in waiting:
        try:
            if batch_api.poll(client, remote_id) != "ended":
                continue
            results = batch_api.collect(client, remote_id)
        except Exception as exc:
            log.error("batch %s: polling the Message Batches API failed (%s); will retry",
                      batch_id, type(exc).__name__)
            continue
        _settle_batch(backend, remote_id, results)
        with engine.connect() as conn:
            rows = _pending_rows(conn, batch_id)
        # Only conversations that were in this Anthropic batch are judged now; any left out to
        # fit the budget are sent in a later pass.
        rows = [row for row in rows if any(cid.startswith(f"c{row.pk}-") for cid in results)]
        for row in rows:
            answers = {}
            for cid, system, text, _prefix in _plan_safely(row):
                status, answer, model, usage = results.get(cid, ("expired", None, None, None))
                if status == "succeeded":
                    answers[batch_api.prompt_hash(system, text)] = (answer, model, usage)
            usage = {}
            result = judge_safely(row.deidentified, row.clinic_id, row.prompt_version,
                                  batch_api.ReplayBackend(answers, backend), usage)
            record_result(engine, row, result, usage)
            judged += 1
        with engine.begin() as conn:
            finish_if_complete(conn, batch_id)
            if _pending_rows(conn, batch_id):        # a remainder waits: let the next pass submit it
                conn.execute(batches.update().where(batches.c.id == batch_id).values(anthropic_batch_id=None))
        log.info("batch %s: Message Batches API results collected, %d conversations judged",
                 batch_id, len(rows))
    return judged


def _settle_batch(backend, remote_id, results):
    """Replace the batch's worst-case reservation with what it really cost (billed at the batch
    discount, including errored or refused items that reported usage)."""
    from .budget import usage_cost
    budget = _budget(backend)
    ledger_id = budget.find_open(remote_id) if budget else None
    if ledger_id is None:
        return
    usd = sum(usage_cost(model or backend.model, usage, batch=True)
              for _status, _text, model, usage in results.values() if usage)
    budget.settle(ledger_id, usd, ref=remote_id)


def process_batch_api(engine, backend, client=None):
    """One pass of the batch-API worker: submit service batches not yet submitted, then collect any
    that have ended. Returns the number of batches submitted plus conversations judged."""
    client = client if client is not None else backend.client
    with engine.connect() as conn:
        before = conn.execute(sa.select(sa.func.count()).select_from(batches)
                              .where(batches.c.anthropic_batch_id.is_not(None))).scalar()
    _submit_new_batches(engine, backend, client)
    with engine.connect() as conn:
        after = conn.execute(sa.select(sa.func.count()).select_from(batches)
                             .where(batches.c.anthropic_batch_id.is_not(None))).scalar()
    return (after - before) + _collect_ended_batches(engine, backend, client)


def finish_if_complete(conn, batch_id):
    """A batch is done when every one of its conversations has a result."""
    waiting = conn.execute(
        sa.select(sa.func.count()).select_from(conversations)
        .where(conversations.c.batch_id == batch_id,
               ~sa.exists().where(judge_results.c.conversation_pk == conversations.c.pk))).scalar()
    if waiting == 0:
        conn.execute(batches.update().where(batches.c.id == batch_id).values(
            status="done", finished_at=utcnow()))


def main():
    ap = argparse.ArgumentParser(description="Process queued batch conversations.")
    ap.add_argument("--once", action="store_true", help="process what is queued, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    backend = BudgetedBackend(production_backend(settings), Budget(engine, settings.spend_limit_usd))
    while True:
        if settings.use_batch_api:
            # --once makes a single pass: a batch still in progress at Anthropic is picked up by
            # the next run (state is in the database).
            done = process_batch_api(engine, backend)
            if args.once:
                return
        else:
            done = process_pending(engine, backend, settings.worker_batch_size)
            if args.once and done == 0:
                return
        if done == 0:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
