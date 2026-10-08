"""The scenario grid meets the counts in brief section 7.4 and is reproducible."""

import re
from collections import Counter

import pytest

from safety_eval.grid import build_cards, load_blueprints, load_cards
from safety_eval.schemas import CRITERIA


@pytest.fixture(scope="module")
def cards():
    return build_cards(load_blueprints())


def test_committed_grid_matches_a_fresh_build(cards):
    # data/scenarios.yaml is generated; editing it by hand would make it drift from the blueprints.
    assert load_cards() == cards


def test_build_is_deterministic(cards):
    assert build_cards(load_blueprints()) == cards


def test_counts(cards):
    types = Counter(c.case_type for c in cards)
    assert len(cards) - types["natural"] == 250
    assert types["natural"] == 30
    assert types["failure"] == 150 and types["hard_negative"] >= 40
    for crit in CRITERIA:
        group = [c for c in cards if c.planted_failure and c.planted_failure.criterion == crit]
        assert len(group) == 30
        assert Counter(c.planted_failure.difficulty for c in group) == {"obvious": 15, "subtle": 15}


def test_ids_are_unique_and_opaque(cards):
    ids = [c.scenario_id for c in cards]
    assert len(ids) == len(set(ids))
    # The ID must reveal nothing about the scenario: random hex only, and the file order is by ID.
    assert all(re.fullmatch(r"cv-[0-9a-f]{6}", i) for i in ids)
    assert ids == sorted(ids)


def test_every_criterion_has_each_responder_type(cards):
    for crit in CRITERIA:
        responders = {c.responder for c in cards
                      if c.planted_failure and c.planted_failure.criterion == crit}
        assert responders == {"assistant", "coach", "support"}, crit


def test_record_constraints_are_respected(cards):
    for c in cards:
        if c.blueprint_id in ("SAF-06", "GRD-12"):
            assert "gliclazide" in c.patient_record.other_medications
        if c.blueprint_id == "GRD-03":
            assert c.patient_record.sex == "male"
        if c.blueprint_id == "GRD-01":
            assert c.patient_record.dose_step == 1 and c.patient_record.weeks_on_treatment <= 3


def test_facts_exist_on_reference_sheet(cards):
    from pathlib import Path
    sheet = (Path(__file__).resolve().parents[1] / "data/reference/medication_facts.md").read_text()
    for c in cards:
        for fact in c.facts:
            assert f"**{fact}**" in sheet, (c.blueprint_id, fact)


def test_timelines_fit_the_situation(cards):
    # A situation like "wants the next dose step" is impossible on the top step (decision D30).
    limits = {bp["id"]: bp["record"] for bp in load_blueprints()}
    for c in cards:
        lim, rec = limits[c.blueprint_id], c.patient_record
        assert lim.get("min_step", 1) <= rec.dose_step <= lim.get("max_step", 5), c.blueprint_id
        assert lim.get("min_weeks", 1) <= rec.weeks_on_treatment <= lim.get("max_weeks", 104), c.blueprint_id
