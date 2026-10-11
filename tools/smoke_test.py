"""Smoke test against the real Claude API, inside the spend limit. Synthetic Harbourline data only.

    JUDGE_ANTHROPIC_API_KEY=... SPEND_LIMIT_USD=5 PYTHONPATH=src python tools/smoke_test.py [--sync N] [--batch N]

(ANTHROPIC_API_KEY also works outside a Claude Code cloud session, where that name is reserved.)

Steps, each stopping the run if it fails, so a broken setup costs almost nothing:
  1. auth check: one tiny request (max_tokens 16) through the same ApiBackend the service uses,
     including the refusal-fallback beta header;
  2. N conversations through POST /v1/evaluate (synchronous, prompt caching);
  3. N conversations through POST /v1/batches with the Message Batches API worker, polled for up to
     --wait minutes (state is in --db, so a later run with --resume collects it);
  4. a report: cost per conversation, cache reads and writes, outcomes, ledger versus limit.

The service database is a local SQLite file; nothing leaves this machine except the API calls.
"""

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safety_eval.deid import deidentify_conversation               # noqa: E402
from safety_eval.llm import ApiBackend, ModelCallError              # noqa: E402
from safety_eval.service import admin                               # noqa: E402
from safety_eval.service.app import create_app                      # noqa: E402
from safety_eval.service.budget import Budget, BudgetedBackend, usage_cost  # noqa: E402
from safety_eval.service.db import judge_results, spend_ledger     # noqa: E402
from safety_eval.service.settings import Settings, production_backend, require_spend_limit  # noqa: E402
from safety_eval.service.worker import process_batch_api            # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "clinics" / "harbourline_conversations.json"


def conversations(n, tag):
    base = json.loads(FIXTURE.read_text())
    out = []
    for i in range(n):
        c = copy.deepcopy(base[i % len(base)])
        c["conversation_id"] = f"smoke-{tag}-{i + 1:02d}"
        out.append(c)
    return out


def auth_check(settings):
    print("1. auth check (one tiny request)...", flush=True)
    backend = production_backend(settings)
    backend.max_tokens = 16
    try:
        resp = backend.complete("Reply with the single word: ok", "ok?")
    except ModelCallError as exc:
        cause = exc.__cause__
        print(f"   FAILED: {exc}")
        if cause is not None:
            print(f"   cause: {type(cause).__name__}: {str(cause)[:300]}")
        sys.exit(2)
    u = resp.raw.get("usage", {})
    print(f"   ok: model={resp.model} reply={resp.text.strip()[:20]!r} usage={u} "
          f"cost=${usage_cost(resp.model, u):.5f}")
    return usage_cost(resp.model, u)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", type=int, default=10)
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--wait", type=float, default=15, help="minutes to wait for the batch")
    ap.add_argument("--db", default=str(ROOT / "smoke.db"))
    ap.add_argument("--resume", action="store_true", help="only collect a batch from an earlier run")
    args = ap.parse_args()

    settings = Settings.from_env({**os.environ, "DATABASE_URL": f"sqlite:///{args.db}"})
    require_spend_limit(settings)
    print(f"model {settings.model}, spend limit ${settings.spend_limit_usd:.2f}, db {args.db}")

    settings = Settings(**{**settings.__dict__, "session_secret": "", "use_batch_api": True})
    backend = production_backend(settings)
    app = create_app(settings, backend, deidentify_conversation)
    engine, budget = app.state.engine, app.state.budget
    worker_backend = BudgetedBackend(backend, budget)

    if not args.resume:
        auth_cost = auth_check(settings)
        b = budget.reserve(0.0, "call", settings.model)          # record the auth check in the ledger
        budget.settle(b, auth_cost, model=settings.model)
        with engine.begin() as conn:
            if not conn.execute(sa.text("select 1 from clinics where id='harbourline'")).first():
                admin.create_clinic(engine, "harbourline")
        key = admin.create_key(engine, "harbourline")
        client = TestClient(app)
        h = {"Authorization": f"Bearer {key}"}

        print(f"2. {args.sync} conversations through /v1/evaluate...", flush=True)
        for conv in conversations(args.sync, "sync"):
            t = time.monotonic()
            r = client.post("/v1/evaluate", headers=h, json={"conversation": conv})
            body = r.json()
            outcome = body.get("result", {}).get("outcome") if r.status_code == 200 else body.get("detail")
            print(f"   {conv['conversation_id']}: {r.status_code} {outcome} ({time.monotonic() - t:.1f}s)",
                  flush=True)
            if r.status_code == 503:
                break

        if args.batch:
            print(f"3. {args.batch} conversations through the Message Batches API...", flush=True)
            r = client.post("/v1/batches", headers=h, json={"conversations": conversations(args.batch, "batch")})
            print(f"   service batch: {r.status_code} {r.json().get('batch_id') or r.json()}", flush=True)

    if args.batch:
        deadline = time.monotonic() + args.wait * 60
        while True:
            process_batch_api(engine, worker_backend)
            with engine.connect() as conn:
                waiting = conn.execute(sa.text("select count(*) from batches where status != 'done'")).scalar()
            if not waiting or time.monotonic() > deadline:
                break
            print("   waiting for the Anthropic batch...", flush=True)
            time.sleep(30)
        print(f"   batches still running: {waiting}" + ("  (rerun with --resume to collect)" if waiting else ""))

    report(engine, budget)


def report(engine, budget):
    print("4. report")
    with engine.connect() as conn:
        rows = conn.execute(sa.select(judge_results.c.outcome, judge_results.c.flagged, judge_results.c.model,
                                      judge_results.c.input_tokens, judge_results.c.output_tokens,
                                      judge_results.c.cache_read_tokens, judge_results.c.cache_write_tokens)).all()
        ledger = conn.execute(sa.select(spend_ledger.c.kind, sa.func.count(), sa.func.sum(spend_ledger.c.usd))
                              .group_by(spend_ledger.c.kind)).all()
    outcomes = {}
    for r in rows:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
    print(f"   judged {len(rows)}: {outcomes}, flagged {sum(bool(r.flagged) for r in rows)}")
    for r in rows[:30]:
        print(f"   in={r.input_tokens} out={r.output_tokens} cache_read={r.cache_read_tokens} "
              f"cache_write={r.cache_write_tokens} model={r.model}")
    for kind, n, usd in ledger:
        print(f"   ledger {kind}: {n} rows, ${usd or 0:.4f}")
    s = budget.summary()
    print(f"   spend: settled ${s['settled_usd']:.4f}, reserved ${s['reserved_usd']:.4f} "
          f"({s['open_reservations']} open), remaining ${s['remaining_usd']:.4f} of ${s['limit_usd']:.2f}")
    if rows:
        print(f"   average per judged conversation: ${s['settled_usd'] / len(rows):.4f} (includes the auth check)")


if __name__ == "__main__":
    main()
