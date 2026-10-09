"""Labelling tool and reconcile reports, driven with scripted answers (no real labels needed)."""

import datetime as dt

import pytest

from safety_eval import labelling, reconcile
from safety_eval.grid import load_cards
from safety_eval.schemas import CRITERIA, HumanLabel


@pytest.fixture(scope="module")
def convs():
    return labelling.load_conversations()


@pytest.fixture
def tmp_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(labelling, "LABELS", tmp_path)
    monkeypatch.setattr(reconcile, "LABELS", tmp_path)
    monkeypatch.setattr(reconcile, "DECISIONS", tmp_path / "reconcile_decisions.jsonl")
    monkeypatch.setattr(reconcile, "FINAL", tmp_path / "final_labels.jsonl")
    monkeypatch.setattr(reconcile, "AMBIGUOUS", tmp_path / "ambiguous.txt")
    return tmp_path


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_display_hides_card_and_source(convs):
    shown = []
    labelling.show(convs[0], out=shown.append)
    text = "\n".join(shown)
    card = next(c for c in load_cards() if c.scenario_id == convs[0].conversation_id)
    for secret in ("planted", "blueprint", "chatgpt", "gemini", "claude", card.blueprint_id, card.situation):
        assert secret.lower() not in text.lower()
    assert convs[0].turns[0].text[:20] in text.replace("\n", " ").replace("         ", "")


def test_label_one_records_answers(convs):
    answers = ["p", "f", "p", "p", "p", "h", "n", "missed red flag"]
    lab = labelling.label_one(convs[0], "pass1", "justin", ask_fn=scripted(answers), out=lambda *_: None,
                              now=dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc))
    assert lab.verdicts["escalation"] == "fail" and lab.severity == "high" and lab.note == "missed red flag"


def test_no_failure_means_no_severity_question(convs):
    lab = labelling.label_one(convs[0], "pass1", "justin", ask_fn=scripted(["p"] * 5 + ["n", ""]),
                              out=lambda *_: None)
    assert lab.severity == "none" and not lab.flagged


def test_pass_resumes_after_quit(convs, tmp_labels):
    few = convs[:3]
    answers = ["p"] * 5 + ["n", ""] + ["q"]           # label one, then quit
    labelling.run_pass(few, "pass1", "justin", ask_fn=scripted(answers), out=lambda *_: None)
    assert len(labelling.load_labels("pass1")) == 1
    labelling.run_pass(few, "pass1", "justin", ask_fn=scripted((["p"] * 5 + ["n", ""]) * 2), out=lambda *_: None)
    assert len(labelling.load_labels("pass1")) == 3


def test_order_is_random_but_fixed_per_pass(convs):
    a = [c.conversation_id for c in labelling.queue(convs, "pass1", {})]
    assert a == [c.conversation_id for c in labelling.queue(convs, "pass1", {})]
    assert a != [c.conversation_id for c in convs]
    assert a != [c.conversation_id for c in labelling.queue(convs, "pass2", {})]


def make_label(cid, fails=(), severity=None, unsure=False, pass_name="pass1"):
    verdicts = {c: ("fail" if c in fails else "pass") for c in CRITERIA}
    return HumanLabel(conversation_id=cid, labeller="t", pass_name=pass_name, rubric_version="0",
                      verdicts=verdicts, severity=severity or ("high" if fails else "none"),
                      unsure=unsure, labelled_at="2026-10-09T00:00:00+00:00")


def test_disagreements_and_plant_success():
    cards = [c for c in load_cards() if c.case_type != "natural"]
    fail_card = next(c for c in cards if c.planted_failure and c.planted_failure.criterion == "scope")
    normal_card = next(c for c in cards if c.case_type == "normal")
    labels = {
        fail_card.scenario_id: make_label(fail_card.scenario_id, fails=()),              # plant missed
        normal_card.scenario_id: make_label(normal_card.scenario_id, fails=("accuracy",), severity="low"),
    }
    items = {d["conversation_id"]: d["reasons"] for d in reconcile.disagreements(cards, labels)}
    assert any("labelled pass" in r for r in items[fail_card.scenario_id])
    assert any("flagged" in r for r in items[normal_card.scenario_id])
    ps = reconcile.plant_success(cards, labels, {})
    assert ps["all"] == {"confirmed": 0, "labelled": 1, "share": 0.0}


def test_finalise_needs_every_decision_and_drops_ambiguous(tmp_labels):
    cards = [c for c in load_cards() if c.case_type != "natural"]
    normal = [c for c in cards if c.case_type == "normal"][:2]
    labels = {normal[0].scenario_id: make_label(normal[0].scenario_id, fails=("accuracy",), severity="low"),
              normal[1].scenario_id: make_label(normal[1].scenario_id)}
    with pytest.raises(SystemExit):
        reconcile.finalise(cards, labels, {})
    decisions = {normal[0].scenario_id: {"conversation_id": normal[0].scenario_id, "decision": "ambiguous"}}
    assert reconcile.finalise(cards, labels, decisions) == (1, 1)


def test_agreement_report():
    first = {f"c{i}": make_label(f"c{i}", fails=("safety",) if i < 5 else ()) for i in range(10)}
    second = {f"c{i}": make_label(f"c{i}", fails=("safety",) if i < 4 else (), pass_name="pass2") for i in range(10)}
    rep = reconcile.agreement(first, second)
    assert rep["conversations"] == 10
    assert rep["criteria"]["safety"]["agreement"] == 0.9
    assert rep["criteria"]["accuracy"]["kappa"] == 1.0
