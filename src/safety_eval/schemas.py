"""Data shapes shared by the whole project.

Two families of objects are kept strictly apart (brief, section 7.4):
- what the judge may see: PatientRecord, Turn, Conversation
- what only the evaluation may see: ScenarioCard (the intended answer)
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CRITERIA = ("safety", "escalation", "accuracy", "scope", "grounding")
Criterion = Literal["safety", "escalation", "accuracy", "scope", "grounding"]
Responder = Literal["assistant", "coach", "support"]
Severity = Literal["none", "low", "high"]
Tier = Literal["none", "routine", "urgent", "emergency"]

AGE_BANDS = ("18-29", "30-44", "45-59", "60-74")
WRITING_STYLES = ("terse", "rambling", "misspellings", "non_native")
PRESENTATIONS = ("direct", "buried", "minimises", "pushes_back")
ANXIETY_LEVELS = ("low", "medium", "high")
MEDICATION = "semaglutide weekly injection"


class Strict(BaseModel):
    # Unknown fields are errors, so a typo in a YAML file fails loudly instead of being dropped.
    model_config = ConfigDict(extra="forbid")


class PatientRecord(Strict):
    """The structured record a responder (and the judge) can see. Grounding is judged against it."""

    age_band: Literal["18-29", "30-44", "45-59", "60-74"]
    sex: Literal["female", "male"]
    medication: str = MEDICATION
    dose_step: int = Field(ge=1, le=5)
    weeks_on_treatment: int = Field(ge=1, le=104)
    conditions: list[str]
    pregnancy_status: Literal["not_applicable", "not_pregnant", "planning"]
    other_medications: list[str]

    @model_validator(mode="after")
    def pregnancy_matches_sex(self):
        if (self.sex == "male") != (self.pregnancy_status == "not_applicable"):
            raise ValueError("pregnancy_status must be not_applicable exactly when sex is male")
        return self


class Persona(Strict):
    """How the patient writes. Used to generate the conversation; never shown to the judge."""

    first_name: str
    anxiety: Literal["low", "medium", "high"]
    writing_style: Literal["terse", "rambling", "misspellings", "non_native"]
    presentation: Literal["direct", "buried", "minimises", "pushes_back"]


class PlantedFailure(Strict):
    criterion: Criterion
    difficulty: Literal["obvious", "subtle"]
    description: str
    # A realistic mistake often breaks more than one rule; these are the other criteria the
    # card's author expects to fail too. The human label, not this list, is the ground truth.
    also_fails: list[Criterion] = []


class ScenarioCard(Strict):
    """The intended answer for one conversation. Stored in data/scenarios.yaml, never shown to the judge."""

    scenario_id: str
    blueprint_id: str
    case_type: Literal["failure", "hard_negative", "normal"]
    theme_id: str
    responder: Responder
    persona: Persona
    patient_record: PatientRecord
    situation: str
    escalation_tier: Tier
    expected_severity: Severity
    planted_failure: PlantedFailure | None
    correct_reply: str | None
    facts: list[str]
    sensitive: bool = False

    @model_validator(mode="after")
    def failure_fields_consistent(self):
        is_failure = self.case_type == "failure"
        if is_failure != (self.planted_failure is not None):
            raise ValueError("planted_failure is required for failure cards and forbidden otherwise")
        if not is_failure and self.correct_reply is None:
            raise ValueError("no-failure cards must say what a correct reply does")
        if is_failure == (self.expected_severity == "none"):
            raise ValueError("failure cards need low or high severity; others need none")
        return self


class Turn(Strict):
    speaker: Literal["patient", "responder"]
    text: str = Field(min_length=1)


class Conversation(Strict):
    """What the judge sees: the conversation, who replied, and the patient record. Nothing else."""

    conversation_id: str
    responder: Responder
    patient_record: PatientRecord
    turns: list[Turn] = Field(min_length=4, max_length=10)

    @model_validator(mode="after")
    def patient_first_reply_last_alternating(self):
        speakers = [t.speaker for t in self.turns]
        if speakers[0] != "patient" or speakers[-1] != "responder":
            raise ValueError("a conversation starts with the patient and ends with a reply")
        if any(a == b for a, b in zip(speakers, speakers[1:])):
            raise ValueError("turns must alternate between patient and responder")
        return self

