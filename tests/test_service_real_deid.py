"""The service wired to the real de-identifier (Phase 2B), not the fake one used elsewhere."""

import copy

from safety_eval.deid import deidentify_conversation
from safety_eval.llm import FakeBackend
from safety_eval.service.settings import production_deidentifier

from conftest import reply

EMAIL, PHONE, NAME = "jo.citizen@example.com", "0412 345 678", "Jo Citizen"


def _with_pii(hl_data):
    data = copy.deepcopy(hl_data[0])
    data["turns"][0]["text"] = f"Hi, my name is Jo. Call me on {PHONE} or email {EMAIL}."
    data["turns"][1]["text"] += " If things get worse, call Lifeline on 13 11 14 or 000."
    return data


def test_production_deidentifier_is_the_real_one():
    assert production_deidentifier() is deidentify_conversation


def test_stored_conversation_is_deidentified_but_keeps_clinic_numbers(make_service, hl_data):
    svc = make_service(deid=deidentify_conversation)
    r = svc.evaluate(_with_pii(hl_data), known_names=[NAME])
    assert r.status_code == 200, r.text
    stored = svc.get(f"/v1/conversations/{r.json()['conversation_pk']}").json()["conversation"]
    text = " ".join(t["text"] for t in stored["turns"])
    for raw in (EMAIL, PHONE, "Jo"):
        assert raw not in text
    assert "[EMAIL_1]" in text and "[PHONE_1]" in text and "[NAME_1]" in text
    assert "13 11 14" in text and "000" in text     # clinic material: Accuracy depends on it
    # the model saw only the de-identified conversation
    sent = svc.backend.calls[0][1]
    assert EMAIL not in sent and PHONE not in sent and "13 11 14" in sent


def test_evidence_quotes_are_checked_against_the_deidentified_text(make_service, hl_data):
    data = _with_pii(hl_data)
    good = reply({"safety": "fail"}, quote="Call me on [PHONE_1]")
    svc = make_service(backend=FakeBackend(good), deid=deidentify_conversation)
    assert svc.evaluate(data).json()["result"]["outcome"] == "ok"
    bad = reply({"safety": "fail"}, quote=f"Call me on {PHONE}")
    svc = make_service(backend=FakeBackend(bad), deid=deidentify_conversation)
    assert svc.evaluate(data).json()["result"]["outcome"] == "needs_review"   # quote not in what the judge saw
