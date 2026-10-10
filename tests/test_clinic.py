"""Clinic configs (roadmap 2A): Fernhill unchanged, a second clinic with nothing hard-coded."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from safety_eval import judge as J
from safety_eval.cache import ResponseCache, cache_key
from safety_eval.clinic import FACT, Clinic, ClinicConfigError, available
from safety_eval.labelling import load_conversations
from safety_eval.llm import FakeBackend
from safety_eval.reference import facts, protocol_text
from safety_eval.schemas import CRITERIA, BaseConversation, Conversation

FIXTURES = Path(__file__).parent / "fixtures"
FERNHILL_WORDS = re.compile(r"fernhill|semaglutide|inject|\bpens?\b|dose step|weight", re.IGNORECASE)


@pytest.fixture(scope="module")
def harbourline():
    return Clinic.load("harbourline")


@pytest.fixture(scope="module")
def hl_convs(harbourline):
    data = json.loads((FIXTURES / "clinics" / "harbourline_conversations.json").read_text())
    return [harbourline.parse_conversation(d) for d in data]


def test_every_clinic_loads_and_its_rubric_parses():
    assert {"fernhill", "harbourline"} <= set(available())
    for cid in available():
        clinic = Clinic.load(cid)
        text = J.rubric_text(clinic=clinic)
        assert all(c.upper() in text for c in CRITERIA)


# ------------------------------------------------------------------ Fernhill unchanged

def test_fernhill_prompts_are_byte_identical_to_before_clinics():
    """Hashes taken before the refactor. If this fails, cached judge answers no longer match
    the prompts they were given: do not update the fixture without a decision in decisions.md."""
    want = json.loads((FIXTURES / "fernhill_render_hashes.json").read_text())
    convs = load_conversations(core_only=False)
    examples = J.EXAMPLES.read_text()
    for version, expected in want.items():
        prompt = J.PromptVersion.load(version, check_leaks=False)
        sets = [list(CRITERIA)] if prompt.mode == "whole" else [[c] for c in CRITERIA]
        h = hashlib.sha256()
        for conv in convs:
            for crits in sets:
                h.update(J.render(prompt, conv, crits, examples).encode())
                h.update(b"\0")
        assert len(convs) == expected["n_conversations"]
        assert h.hexdigest() == expected["sha256"], f"prompt {version} renders differently"


def test_fernhill_cache_keys_are_unchanged():
    conv = load_conversations()[0]
    prompt = J.PromptVersion.load("C", check_leaks=False)
    backend = FakeBackend("{}", model="m")
    old = cache_key(prompt_version="C", prompt_sha=prompt.sha, model="m",
                    conversation_id=conv.conversation_id, criterion="all", attempt=0, repeat=0)
    assert J._key(prompt, backend, conv, "all", 0, 0, Clinic.load("fernhill")) == old


def test_reference_helpers_still_read_fernhill():
    assert len(facts()) == 35 and next(iter(facts())) == "F01"
    assert protocol_text().startswith("## Who replies to patients")


def test_fernhill_still_validates_strictly():
    data = load_conversations()[0].model_dump()
    data["responder"] = "peer"
    with pytest.raises(ValidationError):
        Clinic.load("fernhill").parse_conversation(data)
    assert isinstance(Clinic.load("fernhill").parse_conversation(load_conversations()[0].model_dump()),
                      Conversation)


# ------------------------------------------------------------------ a second clinic

def test_harbourline_prompt_has_its_own_material_and_no_fernhill_text(harbourline, hl_convs):
    prompt = J.PromptVersion.load("C", check_leaks=False)
    # The prompt file's own wording is shared by every clinic; check only what the clinic fills in.
    shared = FERNHILL_WORDS.findall(prompt.template)
    for conv in hl_convs:
        text = J.render(prompt, conv, clinic=harbourline)
        assert "## Who replies to clients" in text
        assert "H01:" in text and "S1" not in text.split("<clinic_protocol>")[0].split("<reference_sheet>")[1]
        assert harbourline.staff[conv.responder] in text
        assert sorted(FERNHILL_WORDS.findall(text)) == sorted(shared), conv.conversation_id


def test_sources_section_is_not_read_as_facts(harbourline):
    ids = list(harbourline.facts())
    assert ids[0] == "H01" and not any(i.startswith("S") for i in ids)


def test_fact_pattern_accepts_lines_with_and_without_source_tags():
    text = "- **H01** Plain fact.\n- **F02** Tagged fact. [S1, S2] `verified 2026-10-08`\n"
    assert dict(FACT.findall(text)) == {"H01": "Plain fact.", "F02": "Tagged fact."}


def test_harbourline_judges_end_to_end_with_a_fake_model(harbourline, hl_convs, tmp_path):
    conv = hl_convs[0]
    quote = conv.turns[1].text[:30]
    reply = {"criteria": {c: {"evidence": [quote], "analysis": "ok", "verdict": "pass"} for c in CRITERIA},
             "overall_flag": False, "severity": "none"}
    backend = FakeBackend(json.dumps(reply))
    prompt = J.PromptVersion.load("C", check_leaks=False)
    cache = ResponseCache("judge", root=tmp_path)
    result = J.judge(conv, prompt, backend, cache=cache, clinic=harbourline)
    assert result.outcome == "ok"
    assert "peer support worker" in backend.calls[0][1] or harbourline.staff[conv.responder] in backend.calls[0][1]
    # cached under a clinic-specific key: a Fernhill conversation with the same id cannot collide
    assert J.needed_calls(prompt, conv, backend, cache, clinic=harbourline) == 0
    assert J.needed_calls(prompt, conv, backend, cache) == 1


def test_editing_a_clinic_file_changes_its_cache_key(tmp_path):
    src = Path(Clinic.load("harbourline").folder)
    folder = tmp_path / "harbourline"
    folder.mkdir()
    for f in src.iterdir():
        (folder / f.name).write_text(f.read_text())
    before = Clinic(folder).sha
    (folder / "protocol.md").write_text((folder / "protocol.md").read_text() + "\nNew rule.\n")
    assert Clinic(folder).sha != before


def test_unknown_responder_role_fails_before_any_model_call(harbourline, hl_convs):
    conv = hl_convs[0].model_copy(update={"responder": "coach"})
    backend = FakeBackend("{}")
    with pytest.raises(ClinicConfigError):
        J.judge(conv, J.PromptVersion.load("C", check_leaks=False), backend, clinic=harbourline)
    assert backend.calls == []


@pytest.mark.parametrize("change, problem", [
    (lambda r: r.pop("program"), "missing record field 'program'"),
    (lambda r: r.update(sessions_completed="three"), "'sessions_completed' should be int"),
    (lambda r: r.update(favourite_colour="blue"), "unknown record field 'favourite_colour'"),
])
def test_record_fields_are_checked(harbourline, hl_convs, change, problem):
    data = hl_convs[0].model_dump()
    change(data["patient_record"])
    with pytest.raises(ValueError, match=re.escape(problem)):
        harbourline.parse_conversation(data)


@pytest.mark.parametrize("value", [True, 2.5, None, [1, 2]])
def test_record_values_are_not_coerced(harbourline, hl_convs, value):
    data = hl_convs[0].model_dump()
    data["patient_record"]["sessions_completed"] = value
    with pytest.raises(ValidationError):
        harbourline.parse_conversation(data)


def test_generic_conversation_keeps_turn_rules():
    with pytest.raises(ValidationError):
        BaseConversation(conversation_id="x", responder="assistant", patient_record={},
                         turns=[{"speaker": "responder", "text": "hi"}] * 4)


def _write_clinic(folder, **overrides):
    src = Clinic.load("harbourline").folder
    folder.mkdir()
    for f in src.iterdir():
        (folder / f.name).write_text(f.read_text())
    import yaml
    cfg = yaml.safe_load((src / "clinic.yaml").read_text())
    cfg.update(overrides)
    cfg = {k: v for k, v in cfg.items() if v is not None}
    (folder / "clinic.yaml").write_text(yaml.safe_dump(cfg))
    return folder


@pytest.mark.parametrize("overrides, message", [
    ({"id": "other"}, "does not match folder"),
    ({"surprise": 1}, "unknown keys"),
    ({"record_schema": "fernhill"}, "exactly one of"),
    ({"protocol_start": "## Not a heading in the file"}, "not found"),
    ({"files": {"rubric": "rubric.md", "reference": "missing.md", "protocol": "protocol.md"}}, "does not exist"),
])
def test_bad_configs_fail_at_load_time(tmp_path, overrides, message):
    folder = _write_clinic(tmp_path / "harbourline", **overrides)
    with pytest.raises(ClinicConfigError, match=message):
        Clinic(folder)
