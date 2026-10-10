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

from .db import batches, conversations, insert_result, judge_results, make_engine, utcnow
from .judging import judge_safely
from .settings import Settings, production_backend

log = logging.getLogger("safety_eval.service")


def process_pending(engine, backend, limit=10):
    """Judge up to `limit` queued conversations. Returns how many were processed."""
    with engine.connect() as conn:
        pending = conn.execute(
            sa.select(conversations.c.pk, conversations.c.clinic_id, conversations.c.batch_id,
                      conversations.c.deidentified, batches.c.prompt_version)
            .join(batches, batches.c.id == conversations.c.batch_id)
            .where(~sa.exists().where(judge_results.c.conversation_pk == conversations.c.pk))
            .order_by(conversations.c.pk).limit(limit)).all()
    for row in pending:
        with engine.begin() as conn:    # mark running before the (slow) model call
            conn.execute(batches.update().where(batches.c.id == row.batch_id,
                                                batches.c.status == "queued").values(status="running"))
        result = judge_safely(row.deidentified, row.clinic_id, row.prompt_version, backend)
        try:
            with engine.begin() as conn:
                insert_result(conn, row.clinic_id, row.pk, result)
                finish_if_complete(conn, row.batch_id)
        except IntegrityError:
            log.warning("result for conversation %s was already recorded", row.pk)
    return len(pending)


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
    engine, backend = make_engine(settings.database_url), production_backend(settings)
    while True:
        done = process_pending(engine, backend, settings.worker_batch_size)
        if args.once and done == 0:
            return
        if done == 0:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
