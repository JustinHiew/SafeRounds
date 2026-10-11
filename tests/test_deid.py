"""De-identification (roadmap 2B). Every detail below is synthetic and obviously fake."""

import copy

import pytest

from safety_eval import judge as J
from safety_eval.clinic import Clinic
from safety_eval.deid import deidentify_conversation, find_pii, medicare_valid
from safety_eval.schemas import BaseConversation, Conversation, CriterionJudgement, Turn

MEDICARE = "2123 45670 1"          # passes the check digit
IHI = "8003 6012 3456 7890"


@pytest.fixture(scope="module")
def hl():
    return Clinic.load("harbourline")


@pytest.fixture(scope="module")
def fh():
    return Clinic.load("fernhill")


def hl_conv(turns, **record):
    rec = {"age_band": "35-44", "program": "peer support", "sessions_completed": 6,
           "safety_plan_on_file": "yes", **record}
    pairs = [("patient" if i % 2 == 0 else "responder", t) for i, t in enumerate(turns)]
    return BaseConversation(conversation_id="d-1", responder="peer", patient_record=rec,
                            turns=[Turn(speaker=s, text=t) for s, t in pairs])


def fh_conv(turns, **record):
    rec = {"age_band": "30-44", "sex": "female", "dose_step": 2, "weeks_on_treatment": 12,
           "conditions": ["asthma"], "pregnancy_status": "not_pregnant",
           "other_medications": ["none"], **record}
    pairs = [("patient" if i % 2 == 0 else "responder", t) for i, t in enumerate(turns)]
    return Conversation(conversation_id="d-2", responder="coach", patient_record=rec,
                        turns=[Turn(speaker=s, text=t) for s, t in pairs])


def scrub_text(clinic, text, names=()):
    out, mapping = deidentify_conversation(hl_conv([text, "Ok.", "Right.", "Bye."]), clinic, names)
    return out.turns[0].text, mapping


FILLER = ["Okay.", "Thanks.", "Bye."]

# (label, text, the original detail that must not survive, expected placeholder)
CASES = [
    ("email", "Write to Jane.Testperson@Example.com please.", "Jane.Testperson@Example.com", "[EMAIL_1]"),
    ("mobile", "My mobile is 0400 000 123 ok.", "0400 000 123", "[PHONE_1]"),
    ("mobile +61", "Ring +61 400 000 123 ok.", "400 000 123", "[PHONE_1]"),
    ("landline brackets", "Home is (02) 5550 0123 ok.", "5550 0123", "[PHONE_1]"),
    ("landline plain", "Home is 03 5550 0123 ok.", "5550 0123", "[PHONE_1]"),
    ("1300", "Try 1300 555 019 ok.", "1300 555 019", "[PHONE_1]"),
    ("1800", "Try 1800 555 019 ok.", "1800 555 019", "[PHONE_1]"),
    ("13 number", "Try 13 22 33 ok.", "13 22 33", "[PHONE_1]"),
    ("medicare with irn", f"Card {MEDICARE} ok.", MEDICARE, "[MEDICARE_1]"),
    ("medicare plain", "Card 2123456701 ok.", "2123456701", "[MEDICARE_1]"),
    ("ihi", f"IHI {IHI} ok.", IHI, "[IHI_1]"),
    ("date slash", "Born 14/02/1990 ok.", "14/02/1990", "[DATE_1]"),
    ("date short", "Born 4/2/90 ok.", "4/2/90", "[DATE_1]"),
    ("date long", "Born 3 March 1985 ok.", "3 March 1985", "[DATE_1]"),
    ("date us", "Born March 3, 1985 ok.", "March 3, 1985", "[DATE_1]"),
    ("url", "See https://example.com/portal/abc?x=1 now.", "example.com/portal", "[URL_1]"),
    ("www", "See www.example.com/portal now.", "example.com/portal", "[URL_1]"),
    ("address", "I live at 12 Placeholder Street ok.", "Placeholder Street", "[ADDRESS_1]"),
    ("address unit", "I live at 4/12 Placeholder Rd, Fakeville NSW 2000 ok.", "Placeholder Rd", "[ADDRESS_1]"),
    ("address crescent", "Send it to 7 Wattle Crescent please.", "Wattle Crescent", "[ADDRESS_1]"),
    ("intro", "Hello, my name is Jane Testperson and I need help.", "Testperson", "[NAME_1]"),
    ("intro im", "Hi, I'm Jane. I need help.", "Jane", "[NAME_1]"),
    ("greeting", "Hi Priya, how are you?", "Priya", "[NAME_1]"),
    ("thanks", "Thanks, Priya. That helps.", "Priya", "[NAME_1]"),
    ("signature", "Take care of yourself.\n- Sam", "Sam", "[NAME_1]"),
]


@pytest.mark.parametrize("label,text,secret,placeholder", CASES, ids=[c[0] for c in CASES])
def test_each_category_in_a_turn(hl, label, text, secret, placeholder):
    out, mapping = scrub_text(hl, text)
    assert secret not in out
    assert placeholder in out
    assert mapping[placeholder] and secret in text
    assert find_pii(out, hl) == []          # running the detectors over the output finds nothing


@pytest.mark.parametrize("label,text,secret,placeholder", CASES, ids=[c[0] for c in CASES])
def test_each_category_in_a_record_field(hl, label, text, secret, placeholder):
    conv = hl_conv(["Hello there.", "Hi.", "Okay.", "Bye."], notes=text, current_supports=[text])
    out, _ = deidentify_conversation(conv, hl)
    assert secret not in out.patient_record["notes"]
    assert secret not in out.patient_record["current_supports"][0]
    assert placeholder in out.patient_record["notes"]


def test_known_names_whole_word_case_insensitive_and_parts(hl):
    out, mapping = scrub_text(hl, "My sister JANE TESTPERSON said hello. Testperson's cat is Jane-like. Janet is fine.",
                              names=("Jane Testperson",))
    assert "JANE" not in out and "TESTPERSON" not in out.upper().replace("[NAME_1]", "")
    assert "Janet" in out                  # a longer word that merely starts with the name is kept
    assert list(mapping) == ["[NAME_1]"]   # first name, surname and full name are one person


def test_same_value_same_placeholder_everywhere(hl):
    conv = hl_conv(
        ["Ring me on 0400 000 123, or email Jane@Example.com.", "Is 0400000123 your number? Or +61 400 000 123?",
         "Yes, JANE@example.com works too.", "Done."],
        notes="Mobile 0400-000-123")
    out, mapping = deidentify_conversation(conv, hl)
    text = " ".join(t.text for t in out.turns) + out.patient_record["notes"]
    assert text.count("[PHONE_1]") == 4 and "[PHONE_2]" not in text
    assert text.count("[EMAIL_1]") == 2 and "[EMAIL_2]" not in text
    assert set(mapping) == {"[PHONE_1]", "[EMAIL_1]"}


def test_different_values_get_different_placeholders(hl):
    out, mapping = scrub_text(hl, "Call 0400 000 123 or 0400 000 456.")
    assert out == "Call [PHONE_1] or [PHONE_2]."
    assert mapping == {"[PHONE_1]": "0400 000 123", "[PHONE_2]": "0400 000 456"}


def test_name_found_by_introduction_is_scrubbed_elsewhere(hl):
    conv = hl_conv(["Hi, my name is Jane Testperson.", "Thanks for that.", "Please tell Jane Testperson's GP.", "Ok."])
    out, _ = deidentify_conversation(conv, hl)
    assert "Jane" not in " ".join(t.text for t in out.turns)


def test_input_not_modified_class_and_untouched_fields_kept(hl, fh):
    conv = hl_conv(["My email is jane@example.com.", "Ok.", "Hi.", "Bye."], notes="jane@example.com")
    before = copy.deepcopy(conv)
    out, _ = deidentify_conversation(conv, hl)
    assert conv == before and out is not conv
    assert type(out) is BaseConversation
    assert out.conversation_id == "d-1" and out.responder == "peer"
    assert out.patient_record["sessions_completed"] == 6 and list(out.patient_record) == list(conv.patient_record)
    f = fh_conv(["Hi, I'm Jane. My email is jane@example.com.", "Hello.", "Ok.", "Bye."],
                conditions=["asthma", "see jane@example.com"])
    out_f, _ = deidentify_conversation(f, fh)
    assert type(out_f) is Conversation
    assert out_f.patient_record.dose_step == 2 and out_f.patient_record.weeks_on_treatment == 12
    assert out_f.patient_record.conditions == ["asthma", "see [EMAIL_1]"]


def test_deterministic(hl):
    text = "Jane Testperson, 0400 000 123, 14/02/1990, jane@example.com, 7 Wattle Crescent"
    conv = hl_conv([text, "Ok.", "Hi.", "Bye."])
    a = deidentify_conversation(conv, hl, ("Jane Testperson",))
    b = deidentify_conversation(conv, hl, ("Jane Testperson",))
    assert a[0] == b[0] and a[1] == b[1]


# ---------------------------------------------------------------------------- clinic material

def test_harbourline_clinic_numbers_and_urls_survive(hl):
    text = ("Our urgent clinician line is 1800 160 401, open 8 am to 8 pm. Lifeline is 13 11 14, "
            "Suicide Call Back Service is 1300 659 467, and in danger call 000 or 112. "
            "More at https://www.lifeline.org.au and www.beyondblue.org.au.")
    out, mapping = scrub_text(hl, text)
    assert out == text and mapping == {}


def test_clinic_number_matches_by_digits_not_spacing(hl):
    out, _ = scrub_text(hl, "Ring 1800-160-401 or 1800160 401 or 13-11-14.")
    assert "[PHONE" not in out


def test_fernhill_clinic_numbers_survive(fh):
    text = ("Out of hours call healthdirect on 1800 022 222, the Poisons Information Centre on 13 11 26, "
            "Lifeline on 13 11 14, or 000.")
    out, mapping = deidentify_conversation(fh_conv(["Help.", text, "Ok.", text]), fh)
    assert out.turns[1].text == text and out.turns[3].text == text and mapping == {}


def test_other_numbers_next_to_clinic_numbers_are_still_scrubbed(hl):
    out, mapping = scrub_text(hl, "Ring 1800 160 401 or my mobile 0400 000 123, or see www.example.com/me.")
    assert out == "Ring 1800 160 401 or my mobile [PHONE_1], or see [URL_1]."
    assert len(mapping) == 2


def test_a_clinic_number_is_not_scrubbed_in_another_clinic(hl, fh):
    # Harbourline's own number is clinic material for Harbourline only.
    text = "Ring 1300 659 467 please."
    assert scrub_text(hl, text)[0] == text
    out, _ = deidentify_conversation(fh_conv([text, "Ok.", "Hi.", "Bye."]), fh)
    assert out.turns[0].text == "Ring [PHONE_1] please."


# ---------------------------------------------------------------------------- false positives

@pytest.mark.parametrize("text", [
    "I'm feeling worried about this.",
    "I'm sorry, I'm not sure. I'm Fine.",
    "I'm Tired. I'm Not Sure what to do.",
    "I am Worried. I'm Still here.",
    "I'm on dose step 2 and it is week 12.",
    "I take it 3 times a day at 8 am and 8:30 pm.",
    "I had 2 injections and lost 5 kg in 12 weeks.",
    "My blood pressure was 120/80 on 3 occasions.",
    "Take 2 tablets at 10 mg every 4 to 6 hours.",
    "Hi there, how are you? Thanks, that helps. Thanks so much.",
    "Thanks, Doctor. Hi everyone, hello again!",
    "Random number 2123456789 is not a Medicare number.",
    "Session 3 on 12 March was good.",
    "Room 12 Street food is great.",
    "Call 000 now. Or 112.",
])
def test_ordinary_text_is_unchanged(hl, text):
    out, mapping = scrub_text(hl, text)
    assert out == text and mapping == {}


def test_medicare_check_digit():
    assert medicare_valid(MEDICARE) and medicare_valid("2123456701")
    assert not medicare_valid("2123 45671 1")
    assert not medicare_valid("0123456701")
    assert not medicare_valid("12345")


# ---------------------------------------------------------------------------- evidence quotes

def judgement(*quotes):
    return {"safety": CriterionJudgement(evidence=list(quotes), analysis="x", verdict="fail")}


def test_evidence_quote_check_works_on_deidentified_text(hl):
    conv = hl_conv(["Hi, my name is Jane Testperson. My mobile is 0400 000 123, I can't sleep.",
                    "Thanks Jane. Our line is 1800 160 401.", "Ok.", "Bye."])
    clean, _ = deidentify_conversation(conv, hl)
    quote = clean.turns[0].text.split(". ")[1]            # "My mobile is [PHONE_1], I can't sleep."
    assert "[PHONE_1]" in quote
    assert J.quote_problems(judgement(quote), clean) == []
    assert J.quote_problems(judgement("Our line is 1800 160 401."), clean) == []   # clinic number kept
    assert J.quote_problems(judgement("My mobile is 0400 000 123, I can't sleep."), clean) != []
    assert J.quote_problems(judgement("Jane Testperson"), clean) != []
