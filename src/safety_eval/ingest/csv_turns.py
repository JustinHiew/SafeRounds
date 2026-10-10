"""CSV, one row per turn.

Required columns: conversation_id, turn (the order, a whole number), speaker (patient or
responder), text, responder. Columns starting `record_` hold the patient record (the value comes
from the conversation's first row in the file; empty cells are left out; int fields are parsed and list fields
are split on ";"). An optional `known_names` column (names separated by ";") feeds
de-identification. Any other column is an error, so a misspelt header fails loudly.
"""

import csv
from pathlib import Path

from .canonical import Candidate, coerce_record, rejected

REQUIRED = ("conversation_id", "turn", "speaker", "text", "responder")
SPEAKERS = {"patient", "responder"}


def load(path, clinic):
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        missing = [c for c in REQUIRED if c not in columns]
        unknown = [c for c in columns if c not in REQUIRED and c != "known_names"
                   and not c.startswith("record_")]
        if missing or unknown:
            raise ValueError(f"{Path(path).name}: missing columns {missing}, unknown columns {unknown}")
        rows = {}
        for row in reader:
            rows.setdefault(row["conversation_id"], []).append(row)
    return [_conversation(cid, group, clinic) for cid, group in rows.items()]


def _conversation(cid, rows, clinic):
    first = rows[0]       # first in the file, whatever its turn number
    try:
        order = [int(r["turn"]) for r in rows]
    except (TypeError, ValueError):
        return rejected(cid, "turn is not a whole number")
    if len(set(order)) != len(order):
        return rejected(cid, "duplicate turn numbers")
    rows = [r for _, r in sorted(zip(order, rows), key=lambda p: p[0])]
    speakers = [(r["speaker"] or "").strip().lower() for r in rows]
    bad = sorted({s for s in speakers if s not in SPEAKERS})
    if bad:
        return rejected(cid, f"unknown speaker value(s) {bad}")
    if len({r["responder"].strip() for r in rows}) != 1:
        return rejected(cid, "responder differs between rows")
    raw = {k[len("record_"):]: (v or "").strip() for k, v in first.items() if k.startswith("record_")}
    record, problems = coerce_record(clinic, raw)
    if problems:
        return rejected(cid, *problems)
    names = tuple(n.strip() for n in (first.get("known_names") or "").split(";") if n.strip())
    data = {"conversation_id": cid, "responder": first["responder"].strip(), "patient_record": record,
            "turns": [{"speaker": s, "text": r["text"]} for s, r in zip(speakers, rows)]}
    return Candidate(cid, data=data, known_names=names)
