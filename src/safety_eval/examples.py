"""Build prompts/examples.md: worked examples for prompt versions D and E (brief 10.2.4).

Examples come from the train split only (a check here, and the leak guard on every prompt load).
One example per kind of case: a failure for each criterion, a hard negative and a normal case, so
the judge sees what fails and also what worrying-but-correct looks like.

The verdicts come from Justin's final labels when they exist. The reasoning is drafted from the
scenario card and marked for Justin to check, because the final wording of the judge prompt is his.

Run: python -m safety_eval.examples
"""

import json
from pathlib import Path

from .grid import load_cards
from .judge import EXAMPLES, render_conversation, render_record
from .labelling import load_conversations
from .schemas import CRITERIA, HumanLabel
from .splits import kind_of, load_splits

ROOT = Path(__file__).resolve().parents[2]
FINAL = ROOT / "data" / "labels" / "final_labels.jsonl"
SOURCES = ROOT / "data" / "labels" / "example_sources.json"
ORDER = ["escalation", "safety", "hard_negative", "accuracy", "scope", "grounding", "normal"]


def final_labels():
    if not FINAL.exists():
        return {}
    return {lab.conversation_id: lab for lab in
            (HumanLabel.model_validate_json(line) for line in FINAL.read_text().splitlines())}


def choose(cards, convs, train):
    """One train conversation per kind; prefer subtle failures, which teach more than obvious ones."""
    by_kind = {}
    for card in sorted(cards, key=lambda c: c.scenario_id):
        if card.scenario_id not in train or card.scenario_id not in convs:
            continue
        kind = kind_of(card)
        better = card.planted_failure and card.planted_failure.difficulty == "subtle"
        if kind not in by_kind or (better and by_kind[kind].planted_failure.difficulty != "subtle"):
            by_kind[kind] = card
    return [by_kind[k] for k in ORDER if k in by_kind]


def verdicts_for(card, label):
    if label:
        return label.verdicts, label.severity, "from Justin's final label"
    fails = {card.planted_failure.criterion} if card.planted_failure else set()
    return ({c: "fail" if c in fails else "pass" for c in CRITERIA}, card.expected_severity,
            "from the scenario card; TODO-JUSTIN replace with your final label")


def reasoning(card):
    if card.planted_failure:
        return (f"The reply fails {card.planted_failure.criterion}: {card.planted_failure.description} "
                "The other criteria pass.")
    return f"Every criterion passes. {card.correct_reply}"


def build():
    train = load_splits()["train"]
    convs = {c.conversation_id: c for c in load_conversations()}
    labels = final_labels()
    blocks, sources = [], []
    for n, card in enumerate(choose(load_cards(), convs, train), 1):
        assert card.scenario_id in train, "examples must come from the train split"
        conv = convs[card.scenario_id]
        verdicts, severity, source = verdicts_for(card, labels.get(card.scenario_id))
        blocks.append("\n".join([
            f"<example number=\"{n}\">",
            f"Responder: {conv.responder}",
            "Patient record:", render_record(conv.patient_record),
            "Conversation:", render_conversation(conv),
            "Verdicts: " + ", ".join(f"{c} {verdicts[c]}" for c in CRITERIA) + f"; severity {severity}",
            f"Reasoning: {reasoning(card)}",
            "</example>",
        ]))
        # Where each example came from is kept beside the prompt, never inside it: words like
        # "scenario card" would tell the judge the data is constructed.
        sources.append({"example": n, "conversation_id": card.scenario_id, "verdicts": source,
                        "reasoning": "drafted by Claude Code from the scenario card; TODO-JUSTIN check"})
    EXAMPLES.write_text("\n\n".join(blocks) + "\n")
    SOURCES.write_text(json.dumps(sources, indent=2) + "\n")
    print(f"wrote {len(blocks)} examples to {EXAMPLES.relative_to(ROOT)} "
          f"({'final labels' if labels else 'scenario cards; labels not finalised yet'})")


if __name__ == "__main__":
    build()
