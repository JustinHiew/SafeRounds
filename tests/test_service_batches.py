"""Batches and the worker: lifecycle, rejected items, fail-closed processing, isolation."""

import json

import sqlalchemy as sa

from conftest import PHRASE, RAW_EMAIL, reply
from safety_eval.llm import FakeBackend, ModelCallError
from safety_eval.service.db import conversations, judge_results
from safety_eval.service.worker import process_pending


def submit(svc, convs, clinic="harbourline", **extra):
    return svc.post("/v1/batches", clinic, json={"conversations": convs, **extra})


def test_batch_lifecycle_with_rejected_items(svc, hl_data):
    invalid = {**hl_data[2], "conversation_id": "hl-bad", "responder": "coach"}
    r = submit(svc, [hl_data[0], invalid, hl_data[1], hl_data[1], "not a dict"], known_names=["Jo"])
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and body["submitted"] == 2
    rejected = {x["index"]: x for x in body["rejected"]}
    assert set(rejected) == {1, 3, 4}
    assert rejected[1]["conversation_id"] == "hl-bad" and rejected[1]["reasons"]
    assert rejected[3]["reasons"] == ["duplicate conversation_id in this batch"]
    assert PHRASE not in r.text and RAW_EMAIL not in r.text

    url = f"/v1/batches/{body['batch_id']}"
    queued = svc.get(url).json()
    assert queued["status"] == "queued" and queued["completed"] == 0
    assert [x["result"] for x in queued["results"]] == [None, None]

    assert process_pending(svc.engine, svc.backend, limit=1) == 1
    mid = svc.get(url).json()
    assert mid["status"] == "running" and mid["completed"] == 1

    assert process_pending(svc.engine, svc.backend, limit=10) == 1
    assert process_pending(svc.engine, svc.backend, limit=10) == 0
    done = svc.get(url).json()
    assert done["status"] == "done" and done["finished_at"] and done["completed"] == 2
    assert done["submitted"] == 2 and done["rejected"] == 3
    assert done["by_outcome"] == {"ok": 2, "unsure": 0, "needs_review": 0} and done["flagged"] == 0
    assert [x["conversation_id"] for x in done["results"]] == ["hl-001", "hl-002"]
    assert all(x["result"]["outcome"] == "ok" for x in done["results"])
    assert len(svc.backend.calls) == 2


def test_batch_conversations_are_deidentified_before_storage(svc, hl_data):
    submit(svc, [hl_data[0]])
    with svc.engine.connect() as conn:
        (row,) = conn.execute(sa.select(conversations)).all()
    assert RAW_EMAIL not in json.dumps(row.deidentified) and "[EMAIL_1]" in json.dumps(row.deidentified)
    process_pending(svc.engine, svc.backend)
    assert RAW_EMAIL not in " ".join(p for _, p in svc.backend.calls)


def test_batch_results_are_paginated(svc, hl_data, fern_data):
    batch = submit(svc, [hl_data[0], hl_data[1], hl_data[2]]).json()["batch_id"]
    process_pending(svc.engine, svc.backend)
    page = svc.get(f"/v1/batches/{batch}", params={"offset": 1, "limit": 1}).json()
    assert [x["conversation_id"] for x in page["results"]] == ["hl-002"]
    assert page["completed"] == 3 and page["offset"] == 1 and page["limit"] == 1
    assert svc.get(f"/v1/batches/{batch}", params={"limit": 0}).status_code == 422
    assert svc.get(f"/v1/batches/{batch}", params={"offset": -1}).status_code == 422


def test_an_all_rejected_batch_is_done_immediately(svc, hl_data):
    body = submit(svc, [{"conversation_id": "x"}]).json()
    assert body["status"] == "done" and body["submitted"] == 0 and len(body["rejected"]) == 1
    assert svc.get(f"/v1/batches/{body['batch_id']}").json()["status"] == "done"


def test_batch_request_limits(svc, hl_data):
    assert submit(svc, []).status_code == 422
    assert submit(svc, [hl_data[1]] * 1001).status_code == 422


def test_worker_fails_closed_per_conversation(make_service, hl_data):
    svc = make_service(backend=FakeBackend([ModelCallError("down"), RuntimeError(f"boom {PHRASE}"),
                                            "garbage", "garbage", reply()]))
    batch = submit(svc, hl_data).json()["batch_id"]
    process_pending(svc.engine, svc.backend)
    body = svc.get(f"/v1/batches/{batch}").json()
    assert body["status"] == "done"
    outcomes = [x["result"]["outcome"] for x in body["results"]]
    assert outcomes == ["needs_review"] * 3          # a call error, an unexpected error, bad JSON twice
    assert body["results"][1]["result"]["reasons"] == ["internal error"]
    assert body["by_outcome"]["ok"] == 0


def test_worker_records_a_flag(make_service, hl_data):
    quote = hl_data[1]["turns"][1]["text"][:30]
    svc = make_service(backend=FakeBackend(reply({"scope": "fail"}, quote)))
    batch = submit(svc, [hl_data[1]]).json()["batch_id"]
    process_pending(svc.engine, svc.backend)
    body = svc.get(f"/v1/batches/{batch}").json()
    assert body["flagged"] == 1 and body["by_outcome"]["ok"] == 1
    with svc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(judge_results)).scalar() == 1


def test_batch_uses_the_prompt_version_it_was_submitted_with(svc, hl_data):
    batch = submit(svc, [hl_data[1]], prompt_version="B").json()["batch_id"]
    process_pending(svc.engine, svc.backend)
    assert svc.get(f"/v1/batches/{batch}").json()["results"][0]["result"]["prompt_version"] == "B"
    assert submit(svc, [hl_data[1]], prompt_version="A").status_code == 422


def test_a_conversation_is_never_judged_twice(svc, hl_data):
    submit(svc, [hl_data[1]])
    process_pending(svc.engine, svc.backend)
    process_pending(svc.engine, svc.backend)
    assert len(svc.backend.calls) == 1
