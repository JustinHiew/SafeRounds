"""Tenant isolation, feedback, stats, audit log, limits, logging and startup of the service."""

import logging
import re
import sys
import types
from pathlib import Path

import pytest
import sqlalchemy as sa

from conftest import PHRASE, RAW_EMAIL, reply
from safety_eval.llm import FakeBackend
from safety_eval.service import app as app_module
from safety_eval.service.db import audit_log, feedback, metadata
from safety_eval.service.settings import Settings, StartupError, production_backend, production_deidentifier
from safety_eval.service.worker import process_pending


# ------------------------------------------------------------------ tenant isolation

def test_clinic_b_can_never_see_clinic_a(svc, hl_data, fern_data):
    result = svc.evaluate(hl_data[0]).json()
    batch = svc.post("/v1/batches", json={"conversations": [hl_data[1]]}).json()["batch_id"]
    process_pending(svc.engine, svc.backend)
    pk, rid = result["conversation_pk"], result["result_id"]
    other = "fernhill"
    # 404, not 403: the other clinic cannot tell that these ids exist
    assert svc.get(f"/v1/conversations/{pk}", other).status_code == 404
    assert svc.get(f"/v1/batches/{batch}", other).status_code == 404
    fb = {"result_id": rid, "reviewer": "x", "action": "confirm"}
    assert svc.post("/v1/feedback", other, json=fb).status_code == 404
    # ... while the owner can
    assert svc.get(f"/v1/conversations/{pk}").status_code == 200
    assert svc.get(f"/v1/batches/{batch}").status_code == 200
    assert svc.post("/v1/feedback", json=fb).status_code == 201
    # stats count only the caller's own results
    svc.evaluate(fern_data, clinic="fernhill")
    mine = svc.get("/v1/clinics/me/stats").json()["daily"][-1]["evaluated"]
    theirs = svc.get("/v1/clinics/me/stats", other).json()["daily"][-1]["evaluated"]
    assert (mine, theirs) == (2, 1)
    # and the audit log recorded nothing for the failed attempts
    with svc.engine.connect() as conn:
        rows = conn.execute(sa.select(audit_log.c.clinic_id)).all()
    assert all(r.clinic_id == "harbourline" for r in rows)


# ------------------------------------------------------------------ feedback

def test_feedback_is_stored(svc, hl_data):
    rid = svc.evaluate(hl_data[1]).json()["result_id"]
    r = svc.post("/v1/feedback", json={"result_id": rid, "reviewer": "Dr Test", "action": "overturn",
                                       "criterion": "safety", "note": "not a real issue"})
    assert r.status_code == 201
    with svc.engine.connect() as conn:
        (row,) = conn.execute(sa.select(feedback)).all()
    assert (row.result_pk, row.reviewer, row.action, row.criterion, row.clinic_id) == \
        (rid, "Dr Test", "overturn", "safety", "harbourline")


@pytest.mark.parametrize("bad", [{"action": "delete"}, {"criterion": "tone"}, {"reviewer": ""},
                                 {"result_id": "abc"}])
def test_bad_feedback_is_422(svc, hl_data, bad):
    rid = svc.evaluate(hl_data[1]).json()["result_id"]
    body = {"result_id": rid, "reviewer": "r", "action": "comment", **bad}
    assert svc.post("/v1/feedback", json=body).status_code == 422


def test_feedback_on_a_missing_result_is_404(svc):
    assert svc.post("/v1/feedback", json={"result_id": 999, "reviewer": "r", "action": "confirm"}).status_code == 404


# ------------------------------------------------------------------ stats

def test_stats_count_outcomes_and_failed_criteria(make_service, hl_data):
    quote = hl_data[1]["turns"][1]["text"][:30]
    svc = make_service(backend=FakeBackend([reply(), reply({"safety": "fail", "scope": "fail"}, quote),
                                            reply({"accuracy": "unsure"}, quote), "garbage", "garbage"]))
    for conv in (hl_data[0], hl_data[1], hl_data[1], hl_data[2]):
        svc.evaluate(conv)
    r = svc.get("/v1/clinics/me/stats", params={"days": 7})
    assert r.status_code == 200 and len(r.json()["daily"]) == 7
    today = r.json()["daily"][-1]
    assert (today["evaluated"], today["flagged"], today["unsure"], today["needs_review"]) == (4, 1, 1, 1)
    assert today["criterion_fails"] == {"safety": 1, "escalation": 0, "accuracy": 0, "scope": 1, "grounding": 0}
    assert all(d["evaluated"] == 0 for d in r.json()["daily"][:-1])
    assert svc.get("/v1/clinics/me/stats", params={"days": 0}).status_code == 422
    assert len(svc.get("/v1/clinics/me/stats").json()["daily"]) == 28


# ------------------------------------------------------------------ audit

def test_reading_conversation_content_is_audited(svc, hl_data):
    pk = svc.evaluate(hl_data[0]).json()["conversation_pk"]
    with svc.engine.connect() as conn:
        assert conn.execute(sa.select(audit_log)).all() == []      # writing is not a read
    r = svc.get(f"/v1/conversations/{pk}")
    assert r.status_code == 200 and "[EMAIL_1]" in r.text and RAW_EMAIL not in r.text
    with svc.engine.connect() as conn:
        (row,) = conn.execute(sa.select(audit_log)).all()
    assert (row.clinic_id, row.action, row.object_id) == ("harbourline", "read_conversation", str(pk))
    assert row.api_key_prefix == svc.keys["harbourline"][:12]
    assert svc.get("/v1/conversations/9999").status_code == 404


def test_reading_batch_results_is_audited(svc, hl_data):
    batch = svc.post("/v1/batches", json={"conversations": [hl_data[1]]}).json()["batch_id"]
    svc.get(f"/v1/batches/{batch}")
    with svc.engine.connect() as conn:
        (row,) = conn.execute(sa.select(audit_log)).all()
    assert (row.action, row.object_id) == ("read_batch_results", batch)


# ------------------------------------------------------------------ limits

def test_default_size_limits_are_2mb_and_20mb():
    s = Settings()
    assert (s.max_evaluate_bytes, s.max_batch_bytes) == (2 * 1024 * 1024, 20 * 1024 * 1024)


def test_oversized_bodies_are_413(make_service, hl_data):
    svc = make_service(max_evaluate_bytes=3000, max_batch_bytes=6000)
    big = {"conversation": {**hl_data[1], "notes": "x" * 4000}}
    assert svc.post("/v1/evaluate", json=big).status_code == 413
    assert svc.post("/v1/batches", json={"conversations": [big] * 3}).status_code == 413
    assert svc.post("/v1/batches", json={"conversations": [hl_data[1]]}).status_code == 202
    assert svc.evaluate(hl_data[1]).status_code == 200
    calls = len(svc.backend.calls)
    # a body with no Content-Length (chunked) is counted as it streams
    chunks = (b"x" * 1000 for _ in range(10))
    assert svc.post("/v1/evaluate", content=chunks).status_code == 413
    assert len(svc.backend.calls) == calls


def test_other_routes_have_a_small_default_limit(make_service):
    svc = make_service(max_other_bytes=500)
    r = svc.post("/v1/feedback", json={"result_id": 1, "reviewer": "r", "action": "comment", "note": "x" * 900})
    assert r.status_code == 413


def test_rate_limit_is_per_key(make_service, hl_data, fern_data):
    svc = make_service(evaluate_per_minute=2, batches_per_minute=1)
    assert [svc.evaluate(hl_data[1]).status_code for _ in range(3)] == [200, 200, 429]
    assert svc.evaluate(hl_data[1]).headers["retry-after"] == "60"
    assert svc.evaluate(fern_data, clinic="fernhill").status_code == 200       # another key is unaffected
    assert svc.post("/v1/batches", json={"conversations": [hl_data[1]]}).status_code == 202
    assert svc.post("/v1/batches", json={"conversations": [hl_data[2]]}).status_code == 429
    assert svc.get("/v1/clinics/me/stats").status_code == 200                  # reads are not limited


def test_unlimited_requests_are_counted_after_auth(make_service, hl_data):
    svc = make_service(evaluate_per_minute=1)
    for _ in range(3):
        assert svc.client.post("/v1/evaluate", json={"conversation": hl_data[1]}).status_code == 401


# ------------------------------------------------------------------ logs

def test_logs_never_contain_conversation_text(make_service, hl_data, caplog):
    caplog.set_level(logging.DEBUG)
    svc = make_service(backend=FakeBackend([reply(), RuntimeError(f"boom {PHRASE}"), "garbage", reply()]))
    assert PHRASE in hl_data[0]["turns"][0]["text"]
    svc.evaluate(hl_data[0])                                       # ok
    svc.evaluate(hl_data[0])                                       # unexpected exception
    svc.evaluate({**hl_data[0], "responder": "coach"})             # invalid
    svc.post("/v1/batches", json={"conversations": [hl_data[0], {**hl_data[0], "responder": "x"}]})
    process_pending(svc.engine, svc.backend)
    svc.get("/v1/conversations/1")
    bad_deid = make_service(deid=__import__("conftest").FakeDeidentifier(fail=True))
    bad_deid.evaluate(hl_data[0])
    assert caplog.records, "expected the service to log something"
    everything = caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert PHRASE not in everything and RAW_EMAIL not in everything and "boom" not in everything


# ------------------------------------------------------------------ startup and docs

def test_importing_the_service_module_builds_nothing():
    assert "app" not in vars(app_module)          # production app is created lazily


def test_production_app_fails_closed_without_a_deidentifier(monkeypatch):
    monkeypatch.setitem(sys.modules, "safety_eval.deid", None)       # makes the import fail
    with pytest.raises(StartupError, match="never run without de-identification"):
        production_deidentifier()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(StartupError, match="de-identification"):
        app_module.get_app()
    with pytest.raises(StartupError, match="de-identification"):
        app_module.app


def test_production_app_fails_without_an_api_key(monkeypatch):
    fake_deid = types.ModuleType("safety_eval.deid")
    fake_deid.deidentify_conversation = lambda conv, clinic, known_names=(): (conv, {})
    monkeypatch.setitem(sys.modules, "safety_eval.deid", fake_deid)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(StartupError, match="ANTHROPIC_API_KEY"):
        production_backend(Settings())
    with pytest.raises(StartupError, match="ANTHROPIC_API_KEY"):
        app_module.get_app()


def test_settings_from_env():
    s = Settings.from_env({"DATABASE_URL": "postgresql+psycopg://x", "PROMPT_VERSION": "B",
                           "RATE_LIMIT_EVALUATE_PER_MIN": "5"})
    assert (s.database_url, s.prompt_version, s.evaluate_per_minute, s.model) == \
        ("postgresql+psycopg://x", "B", 5, "claude-sonnet-5-5")
    with pytest.raises(StartupError):
        Settings.from_env({"RATE_LIMIT_EVALUATE_PER_MIN": "lots"})


def test_openapi_docs_render(svc):
    spec = svc.client.get("/openapi.json").json()
    assert {"/v1/evaluate", "/v1/batches", "/v1/batches/{batch_id}", "/v1/feedback",
            "/v1/clinics/me/stats", "/v1/conversations/{pk}", "/healthz"} <= set(spec["paths"])
    assert svc.client.get("/docs").status_code == 200


def test_sql_file_matches_the_sqlalchemy_tables():
    sql = "\n".join(f.read_text() for f in sorted((Path(__file__).resolve().parents[1] / "sql").glob("*.sql")))
    sql = re.sub(r"--[^\n]*", "", sql)
    in_sql = {}
    for name, body in re.findall(r"create table (\w+) \((.*?)\n\);", sql, re.DOTALL | re.IGNORECASE):
        cols = [line.split()[0] for line in body.strip().splitlines()
                if line.strip() and line.split()[0] not in ("unique", "constraint")]
        in_sql[name] = cols
    assert in_sql.keys() == metadata.tables.keys()
    for name, table in metadata.tables.items():
        assert in_sql[name] == [c.name for c in table.columns], name
    for table in metadata.tables.values():
        for index in table.indexes:
            assert f"create index {index.name} on {table.name}" in sql
