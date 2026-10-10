"""Config-driven adapter for helpdesk-style JSON exports (Intercom, Zendesk, Freshdesk, ...).

A YAML file says where things are, so a new export format is configuration, not code:

    conversations: data.tickets      # dotted path to the list of conversations ("" = the file is the list)
    id: ticket_id                    # field in each conversation
    messages: comments               # field holding the message list
    speaker_field: author.role
    text_field: body
    speaker_map: {user: patient, customer: patient, bot: responder, agent: responder}
    responder: peer                  # a fixed clinic role ...
    # responder_field: assigned.kind # ... or read it from the conversation and map it
    # responder_map: {ai: assistant, human: peer}
    record: {age_band: customer.age_band, program: plan}   # record field -> source path
    known_name_fields: [customer.name, assigned.name]      # fed to de-identification
    merge_consecutive: true          # same speaker twice in a row are joined with a newline
    drop_leading_responder: false    # drop a bot greeting before the first patient message
    drop_trailing_patient: false     # drop patient messages after the last reply

Paths are dotted; a number picks a list item. Unknown keys are an error. Messages with no text
(attachments, empty notes) are skipped.
"""

import json
from pathlib import Path

import yaml

from .canonical import Candidate, coerce_record, rejected

KEYS = {"conversations", "id", "messages", "speaker_field", "text_field", "speaker_map", "responder",
        "responder_field", "responder_map", "record", "known_name_fields", "merge_consecutive",
        "drop_leading_responder", "drop_trailing_patient"}
REQUIRED = {"id", "messages", "speaker_field", "text_field", "speaker_map"}


def load_mapping(source):
    """A validated mapping dict from a YAML path (or an already-loaded dict)."""
    m = dict(source) if isinstance(source, dict) else yaml.safe_load(Path(source).read_text())
    if not isinstance(m, dict):
        raise ValueError("mapping file must be a YAML mapping")
    unknown, missing = set(m) - KEYS, REQUIRED - set(m)
    if unknown or missing:
        raise ValueError(f"mapping: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    if ("responder" in m) == ("responder_field" in m):
        raise ValueError("mapping: set exactly one of responder and responder_field")
    if "responder_field" in m and "responder_map" not in m:
        raise ValueError("mapping: responder_field needs responder_map")
    m["speaker_map"] = {str(k).lower(): v for k, v in m["speaker_map"].items()}
    if not set(m["speaker_map"].values()) <= {"patient", "responder"}:
        raise ValueError("mapping: speaker_map values must be patient or responder")
    m.setdefault("merge_consecutive", True)
    return m


def get_path(obj, path):
    """Value at a dotted path, or None if any step is missing."""
    for step in str(path).split("."):
        if isinstance(obj, dict):
            obj = obj.get(step)
        elif isinstance(obj, list) and step.isdigit() and int(step) < len(obj):
            obj = obj[int(step)]
        else:
            return None
    return obj


def load(path, clinic, mapping):
    m = load_mapping(mapping)
    root = json.loads(Path(path).read_text())
    items = get_path(root, m["conversations"]) if m.get("conversations") else root
    if not isinstance(items, list):
        raise ValueError(f"{Path(path).name}: no list found at {m.get('conversations')!r}")
    return [_conversation(i, item, clinic, m) for i, item in enumerate(items, 1)]


def _conversation(index, item, clinic, m):
    cid = get_path(item, m["id"])
    cid = str(cid) if cid not in (None, "") else f"item {index}"
    messages = get_path(item, m["messages"])
    if not isinstance(messages, list):
        return rejected(cid, "no message list found")
    responder = m.get("responder")
    if responder is None:
        raw = get_path(item, m["responder_field"])
        responder = m["responder_map"].get(str(raw).lower() if raw is not None else None)
        if responder is None:
            return rejected(cid, f"unknown responder value {raw!r}")
    turns = []
    for msg in messages:
        speaker_raw = get_path(msg, m["speaker_field"])
        speaker = m["speaker_map"].get(str(speaker_raw).lower())
        if speaker is None:
            return rejected(cid, f"unknown speaker value {speaker_raw!r}")
        text = get_path(msg, m["text_field"])
        if text is None or (isinstance(text, str) and not text.strip()):
            continue
        if not isinstance(text, str):
            return rejected(cid, "message text is not text")
        turns.append({"speaker": speaker, "text": text.strip()})
    turns = _normalise(turns, m)
    record, problems = coerce_record(clinic, {k: get_path(item, p) for k, p in m.get("record", {}).items()})
    if problems:
        return rejected(cid, *problems)
    names = []
    for path in m.get("known_name_fields", []):
        value = get_path(item, path)
        names += value if isinstance(value, list) else [value]
    names = tuple(n.strip() for n in names if isinstance(n, str) and n.strip())
    data = {"conversation_id": cid, "responder": responder, "patient_record": record, "turns": turns}
    return Candidate(cid, data=data, known_names=names)


def _normalise(turns, m):
    if m["merge_consecutive"]:
        merged = []
        for t in turns:
            if merged and merged[-1]["speaker"] == t["speaker"]:
                merged[-1] = {**t, "text": merged[-1]["text"] + "\n" + t["text"]}
            else:
                merged.append(t)
        turns = merged
    while m.get("drop_leading_responder") and turns and turns[0]["speaker"] == "responder":
        turns = turns[1:]
    while m.get("drop_trailing_patient") and turns and turns[-1]["speaker"] == "patient":
        turns = turns[:-1]
    return turns
