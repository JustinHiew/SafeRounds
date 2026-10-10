"""POST /v1/evaluate: validation, de-identification before storage, fail-closed outcomes."""

import json

import pytest
import sqlalchemy as sa

from conftest import PHRASE, RAW_EMAIL, FakeDeidentifier, reply
from safety_eval.llm import FakeBackend, ModelCallError
from safety_eval.service.db import conversations, judge_results


def stored(svc, table):
    with svc.engine.connect() as conn:
        return conn.execute(sa.select(table)).all()


def test_evaluate_returns_and_stores_a_result(svc, hl_data):
    r = svc.evaluate(hl_data[1])
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["outcome"] == "ok" and body["result"]["conversation_id"] == "hl-002"
    assert set(body["result"]["output"]["criteria"]) == {"safety", "escalation", "accuracy", "scope", "grounding"}
    (row,) = stored(svc, judge_results)
    assert row.pk == body["result_id"] and row.outcome == "ok" and not row.flagged
    assert row.clinic_id == "harbourline" and row.prompt_version == "C"


def test_a_failing_verdict_is_flagged_with_evidence(svc, hl_data):
    quote = hl_data[1]["turns"][1]["text"][:30]
    svc.backend.replies = [reply({"safety": "fail"}, quote)]
    body = svc.evaluate(hl_data[1]).json()
    assert body["result"]["output"]["criteria"]["safety"]["evidence"] == [quote]
    (row,) = stored(svc, judge_results)
    assert row.flagged and row.severity == "high"


def test_conversation_is_deidentified_before_it_is_stored_and_before_the_model(svc, hl_data):
    r = svc.evaluate(hl_data[0], known_names=["Jo"])
    assert r.status_code == 200
    assert svc.deid.calls == [("hl-001", ("Jo",))]
    (row,) = stored(svc, conversations)
    text = json.dumps(row.deidentified)
    assert RAW_EMAIL not in text and "[EMAIL_1]" in text
    sent = " ".join(system + prompt for system, prompt in svc.backend.calls)
    assert RAW_EMAIL not in sent and "[EMAIL_1]" in sent
    assert RAW_EMAIL not in r.text


def test_if_the_deidentifier_breaks_nothing_is_stored_or_sent(make_service, hl_data):
    svc = make_service(deid=FakeDeidentifier(fail=True))
    r = svc.evaluate(hl_data[0])
    assert r.status_code == 500 and PHRASE not in r.text and RAW_EMAIL not in r.text
    assert stored(svc, conversations) == [] and svc.backend.calls == []


def test_invalid_conversations_are_422_with_reasons_and_without_their_text(svc, hl_data):
    bad = json.loads(json.dumps(hl_data[0]))
    bad["responder"] = "coach"                       # not a Harbourline role
    bad["patient_record"]["age_band"] = 30           # wrong type
    bad["patient_record"]["surprise"] = "x"          # unknown field
    r = svc.evaluate(bad)
    assert r.status_code == 422
    assert PHRASE not in r.text and RAW_EMAIL not in r.text
    assert stored(svc, conversations) == [] and svc.deid.calls == []
    short = json.loads(json.dumps(hl_data[0]))
    short["turns"] = short["turns"][:2]
    assert svc.evaluate(short).status_code == 422
    assert svc.post("/v1/evaluate", json={"conversation": hl_data[0], "extra": 1}).status_code == 422


def test_fernhill_conversation_works_for_the_fernhill_clinic_only(svc, fern_data, hl_data):
    assert svc.evaluate(fern_data, clinic="fernhill").status_code == 200
    assert svc.evaluate(fern_data, clinic="harbourline").status_code == 422
    assert svc.evaluate(hl_data[0], clinic="fernhill").status_code == 422


@pytest.mark.parametrize("version", ["A", "ZZ", "../C", "C.md", ""])
def test_unusable_prompt_versions_are_rejected(svc, hl_data, version):
    r = svc.evaluate(hl_data[1], prompt_version=version)
    assert r.status_code == 422 and svc.backend.calls == []


def test_a_requested_prompt_version_is_used(svc, hl_data):
    body = svc.evaluate(hl_data[1], prompt_version="B").json()
    assert body["result"]["prompt_version"] == "B"


def test_default_prompt_must_be_usable(make_service):
    with pytest.raises(ValueError, match="rules baseline"):
        make_service(prompt_version="A")


# ------------------------------------------------------------------ fail closed

@pytest.mark.parametrize("backend", [
    FakeBackend(ModelCallError("timed out")),
    FakeBackend("this is not JSON at all"),
    FakeBackend('{"criteria": {}}'),
], ids=["model_call_error", "garbage", "wrong_shape"])
def test_model_failures_are_needs_review_never_ok(make_service, hl_data, backend):
    svc = make_service(backend=backend)
    body = svc.evaluate(hl_data[1]).json()
    assert body["result"]["outcome"] == "needs_review" and body["result"]["output"] is None
    (row,) = stored(svc, judge_results)
    assert row.outcome == "needs_review"


def test_a_quote_that_is_not_in_the_conversation_is_needs_review(make_service, hl_data):
    svc = make_service(backend=FakeBackend(reply({"safety": "fail"}, "words nobody said")))
    assert svc.evaluate(hl_data[1]).json()["result"]["outcome"] == "needs_review"


def test_unexpected_exception_is_needs_review_with_no_detail(make_service, hl_data):
    svc = make_service(backend=FakeBackend(RuntimeError(f"boom {PHRASE}")))
    r = svc.evaluate(hl_data[1])
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["outcome"] == "needs_review" and result["reasons"] == ["internal error"]
    assert PHRASE not in r.text and "Traceback" not in r.text
    (row,) = stored(svc, judge_results)
    assert row.outcome == "needs_review" and PHRASE not in json.dumps(row.result)


def test_the_service_never_writes_to_the_research_cache(svc, hl_data):
    from safety_eval.cache import CACHE_DIR
    before = sorted(p.name for p in CACHE_DIR.rglob("*"))
    svc.evaluate(hl_data[1])
    assert sorted(p.name for p in CACHE_DIR.rglob("*")) == before
    svc.backend.replies = [reply()]
    svc.evaluate(hl_data[1])
    assert len(svc.backend.calls) == 2           # no cache: each request calls the model


def test_deidentifier_is_required():
    from safety_eval.service.app import create_app
    from safety_eval.service.settings import Settings
    with pytest.raises(TypeError):
        create_app(Settings(), FakeBackend("{}"))
    with pytest.raises(Exception, match="de-identifier is required"):
        create_app(Settings(), FakeBackend("{}"), None)
