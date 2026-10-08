"""The data shapes reject the mistakes that would leak or corrupt labels."""

import pytest
from pydantic import ValidationError

from safety_eval.schemas import Conversation, PatientRecord, Persona, ScenarioCard

RECORD = dict(age_band="30-44", sex="female", dose_step=2, weeks_on_treatment=6,
              conditions=[], pregnancy_status="not_pregnant", other_medications=[])


def turns(*speakers):
    return [{"speaker": s, "text": "hi"} for s in speakers]


def test_pregnancy_status_must_match_sex():
    with pytest.raises(ValidationError):
        PatientRecord(**{**RECORD, "sex": "male"})
    PatientRecord(**{**RECORD, "sex": "male", "pregnancy_status": "not_applicable"})


def test_unknown_fields_are_rejected():
    # A conversation file must not be able to smuggle in a label or scenario field.
    with pytest.raises(ValidationError):
        Conversation(conversation_id="c", responder="coach", patient_record=RECORD,
                     turns=turns("patient", "responder", "patient", "responder"),
                     planted_failure="scope")


def test_conversation_shape():
    ok = turns("patient", "responder", "patient", "responder")
    Conversation(conversation_id="c", responder="coach", patient_record=RECORD, turns=ok)
    for bad in (turns("patient", "responder"),                                   # too short
                turns("responder", "patient", "responder", "patient"),           # wrong start
                turns("patient", "patient", "responder", "responder"),           # not alternating
                turns(*["patient", "responder"] * 6)):                           # too long
        with pytest.raises(ValidationError):
            Conversation(conversation_id="c", responder="coach", patient_record=RECORD, turns=bad)


def test_card_failure_fields_must_agree():
    persona = Persona(first_name="Sam", anxiety="low", writing_style="terse", presentation="direct")
    base = dict(scenario_id="cv-1", blueprint_id="OK-01", case_type="normal", theme_id="T01",
                responder="coach", persona=persona, patient_record=RECORD, situation="s",
                escalation_tier="none", expected_severity="none", planted_failure=None,
                correct_reply="does x", facts=[])
    ScenarioCard(**base)
    with pytest.raises(ValidationError):
        ScenarioCard(**{**base, "correct_reply": None})
    with pytest.raises(ValidationError):
        ScenarioCard(**{**base, "expected_severity": "high"})
    with pytest.raises(ValidationError):
        ScenarioCard(**{**base, "case_type": "failure"})
