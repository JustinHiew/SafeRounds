"""Checks for the writing-style measures, on invented messages (no real data needed)."""

from safety_eval.patient_style import measure, message_body


def test_subject_line_is_dropped():
    assert message_body("SUBJECT: pens\nMESSAGE: where do i get them") == "where do i get them"
    assert message_body("pens\nwhere do i get them") == "where do i get them"
    assert message_body("where do i get them") == "where do i get them"


def test_buried_question_and_counts():
    messages = [
        "I started last week. I feel sick. Is that normal?",  # buried: 2 sentences first
        "is that normal? and what about food?",               # two questions, lower-case start
        "Please tell me the dose for my mother",              # no question mark, family member
    ]
    stats = measure(messages)
    assert stats["n_messages"] == 3
    assert stats["share_question_after_2plus_sentences"] == round(1 / 3, 3)
    assert stats["share_two_or_more_question_marks"] == round(1 / 3, 3)
    assert stats["share_no_question_mark"] == round(1 / 3, 3)
    assert stats["share_asking_for_family_member"] == round(1 / 3, 3)
