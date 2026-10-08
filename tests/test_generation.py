"""Model assignment, prompt pack, import checks and natural-set generation, all offline."""

import json
from collections import Counter, defaultdict

import pytest

import safety_eval.importer as importer
from safety_eval import natural
from safety_eval.cache import ResponseCache
from safety_eval.generation import MODELS, assign_models, batches_for, build_prompt, stratum
from safety_eval.grid import load_cards
from safety_eval.llm import FakeBackend
from safety_eval.reference import facts, protocol_text


@pytest.fixture(scope="module")
def cards():
    return load_cards()


@pytest.fixture(scope="module")
def assignment(cards):
    return assign_models(cards)


def test_every_core_card_assigned_and_natural_excluded(cards, assignment):
    core = {c.scenario_id for c in cards if c.case_type != "natural"}
    assert set(assignment) == core


def test_each_model_gets_an_equal_share_of_every_kind_of_case(cards, assignment):
    per = defaultdict(Counter)
    for c in cards:
        if c.scenario_id in assignment:
            per[stratum(c)][assignment[c.scenario_id]] += 1
    for name, counts in per.items():
        assert set(counts) == set(MODELS), name
        assert max(counts.values()) - min(counts.values()) <= 1, name
    totals = Counter(assignment.values())
    assert max(totals.values()) - min(totals.values()) <= 1


def test_batches_cover_each_model_in_fives(assignment):
    batches = batches_for(assignment)
    flat = [i for ids in batches.values() for i in ids]
    assert sorted(flat) == sorted(assignment)
    for name, ids in batches.items():
        assert 1 <= len(ids) <= 5
        assert {assignment[i] for i in ids} == {name.split("_")[0]}


def test_prompt_has_required_sections(cards, assignment):
    batch = [c for c in cards if c.scenario_id in assignment][:5]
    prompt = build_prompt(batch, facts(), protocol_text())
    for section in ("## Purpose", "## Rules", "## Clinic rules", "## Output format", "## Scenarios"):
        assert section in prompt
    for c in batch:
        assert f'conversation_id="{c.scenario_id}"' in prompt
        # Each card shows either the planted mistake or what a correct reply does, never both.
        block = prompt.split(f'conversation_id="{c.scenario_id}"')[1].split("</scenario>")[0]
        assert ("<planted_mistake" in block) != ("<no_mistake>" in block)


def turns(n=4):
    return [{"speaker": "patient" if i % 2 == 0 else "responder", "text": f"message {i}"} for i in range(n)]


def test_extract_json_handles_fences_and_prose():
    data = [{"conversation_id": "x", "turns": turns()}]
    wrapped = "Here you go!\n```json\n" + json.dumps(data) + "\n```\nHope this helps."
    assert importer.extract_json(wrapped) == data
    with pytest.raises(ValueError):
        importer.extract_json("sorry, I can't do that")


def test_check_item_rejections(cards):
    card = next(c for c in cards if c.case_type == "normal")
    good = {"conversation_id": card.scenario_id, "turns": turns()}
    assert importer.check_item(good, card, "gemini", "gemini")[1] is None
    leaky = {"conversation_id": card.scenario_id,
             "turns": turns()[:3] + [{"speaker": "responder", "text": "Per F06 you should wait."}]}
    cases = [
        ({**good, "label": "x"}, card, "gemini", "gemini"),          # extra key
        (good, None, "gemini", "gemini"),                           # unknown ID
        (good, card, "chatgpt", "gemini"),                          # wrong model
        ({**good, "turns": turns(2)}, card, "gemini", "gemini"),    # too short
        (leaky, card, "gemini", "gemini"),                          # leaks a fact ID
    ]
    for item, c, expected, source in cases:
        assert importer.check_item(item, c, expected, source)[1] is not None


def test_leak_patterns_allow_ordinary_patient_words():
    ok = [{"speaker": "patient", "text": "I made a mistake and injected twice, obviously not great"}]
    assert importer.leak_reasons(ok) == []
    assert importer.leak_reasons([{"speaker": "responder", "text": "As Claude, I think..."}])


def test_natural_generation_alternates_and_uses_cache(cards, tmp_path):
    card = next(c for c in cards if c.case_type == "natural")
    cache = ResponseCache("natural", root=tmp_path)
    patient = FakeBackend("patient says hi", model="fake-patient")
    responder = FakeBackend("clinic replies", model="fake-responder")
    got, models = natural.generate_one(card, patient, responder, cache, facts(), protocol_text())
    assert [t["speaker"] for t in got] == ["patient", "responder"] * 2
    assert models == {"patient": "fake-patient", "responder": "fake-responder"}
    # The responder is set up like a deployed clinic assistant, with no hint of a test.
    system = responder.calls[0][0]
    assert "<clinic_protocol>" in system and "test" not in system.lower()
    calls_before = len(patient.calls) + len(responder.calls)
    natural.generate_one(card, patient, responder, cache, facts(), protocol_text())
    assert len(patient.calls) + len(responder.calls) == calls_before   # all from cache


def test_walk_back_is_flagged():
    walked = turns()[:3] + [{"speaker": "responder", "text": "Sorry, I shouldn't have suggested halving it."}]
    assert importer.walk_back_warning(walked)
    assert not importer.walk_back_warning(turns())


def test_chat_app_link_artefacts_are_leaks():
    bad = [{"speaker": "responder", "text": "Lifeline on [13 11 14](tel:+61131114?oai_link_source=model_response_hotline)"}]
    assert "app link artefact" in importer.leak_reasons(bad)
    assert importer.leak_reasons([{"speaker": "responder", "text": "Lifeline on 13 11 14."}]) == []
