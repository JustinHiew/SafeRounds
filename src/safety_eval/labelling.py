"""Blind labelling tool (brief section 8).

Shows one conversation at a time with its responder type and patient record, in a random order
fixed per pass. It never shows the scenario card, the planted mistake or the source model.
Every answer is saved immediately, so quitting and restarting picks up where you stopped.

Commands:
  python -m safety_eval.labelling explore              read 30 random conversations, write free notes
  python -m safety_eval.labelling label                main pass over all core conversations (pass1)
  python -m safety_eval.labelling relabel              self-consistency: 40 random, first labels hidden
  python -m safety_eval.labelling label --pass rater2 --labeller NAME --ids FILE
                                                       a second person labels a given list
  python -m safety_eval.labelling label --redo ID      relabel one conversation (after reconcile)
"""

import argparse
import datetime as dt
import json
import random
import re
import textwrap
from pathlib import Path

from .schemas import CRITERIA, Conversation, HumanLabel

ROOT = Path(__file__).resolve().parents[2]
CONVERSATIONS = ROOT / "data" / "conversations" / "conversations.jsonl"
PROVENANCE = ROOT / "data" / "provenance" / "source_models.csv"
LABELS = ROOT / "data" / "labels"
NOTES = LABELS / "exploration_notes.md"
RUBRIC = ROOT / "docs" / "rubric.md"
RESPONDER = {"assistant": "AI assistant", "coach": "Health coach", "support": "Patient support agent"}
SEEDS = {"pass1": 1, "pass2": 2, "explore": 3}


def rubric_version():
    match = re.search(r"\*\*Version (\w+)", RUBRIC.read_text())
    return match.group(1) if match else "unknown"


def load_conversations(core_only=True):
    """Conversations in ID order. Core only by default: the natural set is labelled separately."""
    import csv
    sets = {r["conversation_id"]: r["set"] for r in csv.DictReader(PROVENANCE.open())}
    convs = [Conversation.model_validate_json(line) for line in CONVERSATIONS.read_text().splitlines()]
    return [c for c in convs if not core_only or sets.get(c.conversation_id) == "core"]


def show(conv, out=print):
    """Render what the labeller may see: role, record and turns. Nothing else."""
    r = conv.patient_record
    out("\n" + "=" * 78)
    out(f"Responder: {RESPONDER[conv.responder]}")
    out(f"Patient record: {r.age_band}, {r.sex}, {r.medication}, dose step {r.dose_step}, "
        f"week {r.weeks_on_treatment}, pregnancy: {r.pregnancy_status}")
    out(f"  conditions: {', '.join(r.conditions) or 'none'}")
    out(f"  other medicines: {', '.join(r.other_medications) or 'none'}")
    out("-" * 78)
    for t in conv.turns:
        who = "PATIENT" if t.speaker == "patient" else "STAFF  "
        body = textwrap.fill(t.text, 70, subsequent_indent=" " * 9)
        out(f"{who}  {body}")
    out("=" * 78)


def ask(question, options, ask_fn=input):
    """Keep asking until the answer is one of the options (first letters accepted)."""
    while True:
        answer = ask_fn(f"{question} [{'/'.join(options)}] ").strip().lower()
        for opt in options:
            if answer and opt.startswith(answer):
                return opt


def label_one(conv, pass_name, labeller, ask_fn=input, out=print, now=None):
    show(conv, out)
    verdicts = {c: ask(f"{c:<10}", ["pass", "fail"], ask_fn) for c in CRITERIA}
    if "fail" in verdicts.values():
        severity = ask("severity  ", ["low", "high"], ask_fn)
    else:
        severity = "none"
    unsure = ask("unsure?   ", ["no", "yes"], ask_fn) == "yes"
    note = ask_fn("note (optional): ").strip()
    return HumanLabel(conversation_id=conv.conversation_id, labeller=labeller, pass_name=pass_name,
                      rubric_version=rubric_version(), verdicts=verdicts, severity=severity,
                      unsure=unsure, note=note,
                      labelled_at=(now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"))


def labels_path(pass_name):
    return LABELS / f"labels_{pass_name}.jsonl"


def load_labels(pass_name):
    """{conversation_id: HumanLabel}; a later line for the same ID (a redo) replaces an earlier one."""
    path = labels_path(pass_name)
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            lab = HumanLabel.model_validate_json(line)
            out[lab.conversation_id] = lab
    return out


def queue(convs, pass_name, done, ids=None):
    """Random order fixed per pass, skipping what is already labelled."""
    pool = [c for c in convs if (ids is None or c.conversation_id in ids)]
    rng = random.Random(SEEDS.get(pass_name, sum(map(ord, pass_name))))
    rng.shuffle(pool)
    return [c for c in pool if c.conversation_id not in done]


def run_pass(convs, pass_name, labeller, ids=None, ask_fn=input, out=print):
    done = load_labels(pass_name)
    todo = queue(convs, pass_name, done, ids)
    out(f"{len(done)} labelled, {len(todo)} to go. Type q at any prompt to stop (progress is saved).")
    LABELS.mkdir(parents=True, exist_ok=True)
    for conv in todo:
        try:
            lab = label_one(conv, pass_name, labeller, ask_fn=_quit_aware(ask_fn), out=out)
        except _Quit:
            out("Stopped. Run the same command to continue.")
            return
        with labels_path(pass_name).open("a") as f:
            f.write(lab.model_dump_json() + "\n")
    out(f"Pass {pass_name} complete ({len(load_labels(pass_name))} labelled).")


def relabel_ids(seed=40, n=40):
    """The self-consistency sample: n random conversations from those labelled in pass1."""
    first = sorted(load_labels("pass1"))
    return set(random.Random(seed).sample(first, min(n, len(first))))


def explore(convs, n=30, ask_fn=input, out=print):
    """Brief 8.1: read with no rubric and note what goes wrong. Notes go to a markdown file."""
    pool = list(convs)
    random.Random(SEEDS["explore"]).shuffle(pool)
    LABELS.mkdir(parents=True, exist_ok=True)
    with NOTES.open("a") as f:
        for conv in pool[:n]:
            show(conv, out)
            note = ask_fn("What, if anything, goes wrong here? (enter to skip, q to stop) ").strip()
            if note.lower() == "q":
                return
            if note:
                f.write(f"- {conv.conversation_id}: {note}\n")


class _Quit(Exception):
    pass


def _quit_aware(ask_fn):
    def wrapped(prompt):
        answer = ask_fn(prompt)
        if answer.strip().lower() == "q":
            raise _Quit
        return answer
    return wrapped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["explore", "label", "relabel"])
    p.add_argument("--pass", dest="pass_name", default="pass1")
    p.add_argument("--labeller", default="justin")
    p.add_argument("--ids", help="file with one conversation ID per line")
    p.add_argument("--redo", help="relabel this one conversation in the given pass")
    a = p.parse_args()
    convs = load_conversations()
    if a.command == "explore":
        explore(convs)
    elif a.command == "relabel":
        run_pass(convs, "pass2", a.labeller, ids=relabel_ids())
    elif a.redo:
        conv = next(c for c in convs if c.conversation_id == a.redo)
        lab = label_one(conv, a.pass_name, a.labeller)
        with labels_path(a.pass_name).open("a") as f:
            f.write(lab.model_dump_json() + "\n")
    else:
        ids = set(Path(a.ids).read_text().split()) if a.ids else None
        run_pass(convs, a.pass_name, a.labeller, ids=ids)


if __name__ == "__main__":
    main()
