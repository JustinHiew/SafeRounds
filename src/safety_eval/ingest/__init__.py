"""Bring conversations in from outside, de-identified (roadmap 2B).

    result = ingest("export.csv", "csv", Clinic.load("harbourline"))
    result.accepted    # validated, de-identified conversations
    result.rejected    # [(conversation_id, [reasons])]

Order for each conversation: normalise (the format's importer) -> check the shape against the
clinic -> de-identify -> check again. A conversation outside 4-10 turns is rejected with a reason,
never truncated, because cutting turns would change what the judge sees. De-identification happens
before anything is returned, so nothing that leaves this function holds the original details.

The placeholder -> original mapping from de-identification is deliberately thrown away here. If it
were returned it would end up in a log, a cache or a queue, and then a store of "de-identified"
text would be one lookup away from identified text. A deployment that must reverse the change can
call deidentify_conversation itself and keep the mapping in the customer's own store.

Rejection reasons never contain conversation text, so reports and logs are safe to share.

    python -m safety_eval.ingest --clinic harbourline --format csv INPUT --out accepted.jsonl --report report.json
"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ..clinic import Clinic, ClinicConfigError
from ..deid import deidentify_conversation
from . import canonical, csv_turns, mapped

MIN_TURNS, MAX_TURNS = 4, 10
FORMATS = ("csv", "json", "mapped")


@dataclass
class ImportResult:
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)      # [(conversation_id, [reasons])]


def _reasons(exc):
    """Messages without the offending input, which could be conversation text."""
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc']) or 'conversation'}: {e['msg']}"
                for e in exc.errors()]
    return [str(exc)]


def _length_problem(turns):
    n = len(turns) if isinstance(turns, list) else 0
    if n < MIN_TURNS:
        return f"too short: {n} turns, need {MIN_TURNS} to {MAX_TURNS}"
    if n > MAX_TURNS:
        return f"too long: {n} turns, the limit is {MAX_TURNS} (conversations are never truncated)"
    return None


def _process(cand, clinic):
    """The accepted conversation, or a list of reasons it was rejected."""
    problem = _length_problem(cand.data.get("turns"))
    if problem:
        return [problem]
    try:
        conv = clinic.parse_conversation(cand.data)
        clean, _discarded = deidentify_conversation(conv, clinic, cand.known_names)
        return clinic.parse_conversation(clean.model_dump())
    except (ValidationError, ValueError, ClinicConfigError) as exc:
        return _reasons(exc)


def ingest(path, fmt, clinic, mapping=None):
    if fmt == "csv":
        candidates = csv_turns.load(path, clinic)
    elif fmt == "json":
        candidates = canonical.load(path)
    elif fmt == "mapped":
        if mapping is None:
            raise ValueError("format 'mapped' needs a mapping file")
        candidates = mapped.load(path, clinic, mapping)
    else:
        raise ValueError(f"unknown format {fmt!r}; use one of {FORMATS}")
    result, seen = ImportResult(), set()
    for cand in candidates:
        if cand.reasons:
            outcome = cand.reasons
        elif cand.conversation_id in seen:
            outcome = ["duplicate conversation_id"]
        else:
            outcome = _process(cand, clinic)
        seen.add(cand.conversation_id)
        if isinstance(outcome, list):
            result.rejected.append((cand.conversation_id, outcome))
        else:
            result.accepted.append(outcome)
    return result


def report(result, clinic, fmt):
    """Counts and reasons only, never conversation text."""
    why = Counter(r for _, reasons in result.rejected for r in reasons)
    return {"clinic": clinic.id, "format": fmt, "accepted": len(result.accepted),
            "rejected": len(result.rejected), "reasons": dict(sorted(why.items())),
            "rejections": [{"conversation_id": cid, "reasons": r} for cid, r in result.rejected]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m safety_eval.ingest",
                                 description="Import conversations, de-identified.")
    ap.add_argument("input")
    ap.add_argument("--clinic", required=True)
    ap.add_argument("--format", required=True, choices=FORMATS)
    ap.add_argument("--mapping", help="YAML mapping file (format mapped)")
    ap.add_argument("--out", required=True, help="accepted conversations, JSONL")
    ap.add_argument("--report", required=True, help="counts and rejection reasons, JSON")
    args = ap.parse_args(argv)
    clinic = Clinic.load(args.clinic)
    result = ingest(args.input, args.format, clinic, args.mapping)
    Path(args.out).write_text("".join(json.dumps(c.model_dump()) + "\n" for c in result.accepted))
    rep = report(result, clinic, args.format)
    Path(args.report).write_text(json.dumps(rep, indent=2) + "\n")
    print(f"accepted {rep['accepted']}, rejected {rep['rejected']}")
