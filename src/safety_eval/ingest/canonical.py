"""The canonical format, plus what every importer shares.

An importer turns its input into Candidates: plain dicts in the BaseConversation shape, still
unvalidated. Validation, de-identification and rejection all happen once, in ingest(), so a new
format cannot skip a step. A Candidate whose own parsing failed carries `reasons` instead of data.
"""

import json
import typing
from dataclasses import dataclass, field
from pathlib import Path

from ..schemas import PatientRecord


@dataclass
class Candidate:
    conversation_id: str
    data: dict | None = None
    known_names: tuple = ()
    reasons: list = field(default_factory=list)


def rejected(conversation_id, *reasons):
    return Candidate(str(conversation_id), reasons=list(reasons))


def load(path):
    """A JSON list, or JSONL (one conversation per line, chosen by the .jsonl extension)."""
    path = Path(path)
    text = path.read_text()
    if path.suffix == ".jsonl":
        items = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                raise ValueError(f"{path.name} line {n}: not valid JSON") from None
    else:
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"{path.name}: not valid JSON") from None
        if not isinstance(items, list):
            raise ValueError(f"{path.name}: expected a JSON list of conversations")
    out = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            out.append(rejected(f"item {i}", "not a JSON object"))
        else:
            out.append(Candidate(str(item.get("conversation_id", f"item {i}")), data=item))
    return out


# ------------------------------------------------------------------------ record field types

def record_types(clinic):
    """{field: "str" | "int" | "list"} for the clinic's patient record."""
    if clinic.record_fields:
        return {k: v["type"] for k, v in clinic.record_fields.items()}
    types = {}
    for name, info in PatientRecord.model_fields.items():
        ann = info.annotation
        types[name] = ("list" if typing.get_origin(ann) is list
                       else "int" if ann is int else "str")
    return types


def coerce_record(clinic, raw):
    """Turn text (CSV) or loosely typed (helpdesk) values into the clinic's field types.
    Empty values are omitted. Returns (record, problems)."""
    types, record, problems = record_types(clinic), {}, []
    for name, value in raw.items():
        if value is None or (isinstance(value, (str, list)) and not value):
            continue
        want = types.get(name, "str")     # an unknown field is reported by the clinic check
        try:
            record[name] = _coerce(value, want)
        except ValueError:
            problems.append(f"record field {name!r} should be {want}")
    return record, problems


def _coerce(value, want):
    if want == "int":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        return int(str(value).strip())
    if want == "list":
        if isinstance(value, str):
            return [p.strip() for p in value.split(";") if p.strip()]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return value
        raise ValueError
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError
    return str(value)
