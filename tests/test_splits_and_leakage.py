"""The split is grouped, complete and reproducible, and no dev/test conversation reaches a prompt."""

import json

import pytest

from safety_eval import leakage
from safety_eval.grid import load_cards
from safety_eval.splits import ASSIGNMENT, assign_groups, family_of, kind_of, load_splits


@pytest.fixture(scope="module")
def core():
    return [c for c in load_cards() if c.case_type != "natural"]


@pytest.fixture(scope="module")
def sources():
    return json.loads(ASSIGNMENT.read_text())["assignment"]


def test_committed_split_matches_fresh_build(core, sources):
    fresh = assign_groups(core, sources)
    committed = load_splits()
    assert {cid: s for s, ids in committed.items() for cid in ids} == fresh


def test_splits_partition_the_core_set(core):
    splits = load_splits()
    assert not (splits["train"] & splits["dev"]) and not (splits["train"] & splits["test"])
    assert not (splits["dev"] & splits["test"])
    assert set().union(*splits.values()) == {c.scenario_id for c in core}


def test_families_never_cross_splits(core):
    split_of = {cid: s for s, ids in load_splits().items() for cid in ids}
    fam = family_of()
    seen = {}
    for c in core:
        g = fam.get(c.blueprint_id, c.blueprint_id)
        assert seen.setdefault(g, split_of[c.scenario_id]) == split_of[c.scenario_id], g


def test_train_has_every_kind_of_case(core):
    train = load_splits()["train"]
    kinds = {kind_of(c) for c in core if c.scenario_id in train}
    assert kinds == {"safety", "escalation", "accuracy", "scope", "grounding", "hard_negative", "normal"}


def test_natural_set_is_never_split():
    natural = {c.scenario_id for c in load_cards() if c.case_type == "natural"}
    assert not natural & set().union(*load_splits().values())


def test_prompt_files_contain_no_dev_or_test_conversation():
    # Brief section 9: this must fail if a test ID (or text) appears in any prompt file.
    leakage.check_prompt_files()


def test_leak_finder_catches_ids_and_pasted_text():
    protected = {"cv-abc123": ["I have had a pounding heartbeat at rest for three days and I am a bit"]}
    assert leakage.find_leaks("Example: cv-abc123 ...", protected) == [("cv-abc123", "id")]
    pasted = "Patient: I have had a pounding heartbeat at rest for three days and I am a bit worried"
    assert leakage.find_leaks(pasted, protected) == [("cv-abc123", "text")]
    assert leakage.find_leaks("an unrelated example", protected) == []
