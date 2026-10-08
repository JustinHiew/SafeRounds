"""Import generated conversations, validate them, and record which model wrote each one.

Run: python -m safety_eval.importer

Reads every reply saved under generation/raw/<model>/ (chatgpt, gemini, claude, natural),
checks each conversation, and writes:
  data/conversations/conversations.jsonl   what the judge may see: ID, responder, record, turns
  data/provenance/source_models.csv        which model wrote each conversation (judge never sees it)
  reports/import_report.json               accepted, rejected with reasons, and still missing

A conversation is rejected rather than repaired: a fixed-up conversation would no longer be
what the generator wrote. Rejected and missing scenarios go into a redo pack instead.
"""

import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import ValidationError

from .grid import load_cards
from .schemas import Conversation

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "generation" / "raw"
CONVERSATIONS = ROOT / "data" / "conversations" / "conversations.jsonl"
PROVENANCE = ROOT / "data" / "provenance" / "source_models.csv"
REPORT = ROOT / "reports" / "import_report.json"
QUALITY = ROOT / "reports" / "generation_quality.json"
SOURCES = ("chatgpt", "gemini", "claude", "natural")

# Text that must never appear inside a conversation: it would tell the judge the answer or the
# author. Ordinary words such as "mistake" are allowed, because patients use them.
LEAK_PATTERNS = {
    "fact or scenario ID": r"\bF\d{2}\b|cv-[0-9a-f]{6}|\b(SAF|ESC|ACC|SCO|GRD|HN|OK|NAT)-\d{2}\b",
    "test-set wording": r"planted|scenario|test set|safety checker|\bcriterion\b|\(subtle\)|\(obvious\)|TODO",
    "model or company name": r"\b(chatgpt|openai|gpt-?\d|gemini|claude|anthropic)\b",
    "brand name": r"\b(wegovy|ozempic|rybelsus|mounjaro|zepbound|saxenda)\b",
    "markup or note": r"^\s*(note|label|commentary)\s*:|\[[^\]]*(mistake|error|fail)[^\]]*\]",
    # Chat apps turn phone numbers into links when copied, e.g. "[13 11 14](tel:...?oai_link_source=...)";
    # "oai" names the generator, and markdown links appear in no real clinic chat (decision D32).
    "app link artefact": r"\]\((tel|https?|mailto):|oai_|utm_source=",
}


# Phrases suggesting a staff reply took back an earlier statement. A walked-back planted mistake
# blurs the label, so these are flagged for a person to look at (a warning, not a rejection:
# legitimate replies can use them too).
WALK_BACK = r"shouldn'?t have (said|suggested|told)|I was wrong|correction:|to correct what I said|ignore what I said|I misspoke|apologies for the confusion"


def walk_back_warning(turns):
    staff = "\n".join(t["text"] for t in turns if t["speaker"] == "responder")
    return bool(re.search(WALK_BACK, staff, re.IGNORECASE))


def extract_json(text):
    """Pull the JSON list out of a chat reply, which may wrap it in ```json fences or prose."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("["), candidate.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON list found")
    data = json.loads(candidate[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("top level is not a list")
    return data


def leak_reasons(turns):
    text = "\n".join(t["text"] for t in turns)
    return [name for name, pattern in LEAK_PATTERNS.items()
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE)]


def check_item(item, card, expected_source, source):
    """Return (Conversation, None) if valid, or (None, reason)."""
    if not isinstance(item, dict) or set(item) != {"conversation_id", "turns"}:
        return None, "item must have exactly the keys conversation_id and turns"
    if card is None:
        return None, "unknown conversation_id"
    if expected_source != source:
        return None, f"assigned to {expected_source}, not {source}"
    try:
        conv = Conversation(conversation_id=card.scenario_id, responder=card.responder,
                            patient_record=card.patient_record, turns=item["turns"])
    except ValidationError as exc:
        return None, "invalid shape: " + "; ".join(e["msg"] for e in exc.errors()[:3])
    leaks = leak_reasons(item["turns"])
    if leaks:
        return None, "leaks " + ", ".join(leaks)
    return conv, None


def raw_files():
    """(source, path) for every saved reply, in name order so a later redo file wins."""
    for source in SOURCES:
        folder = RAW / source
        if folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                yield source, path


def model_name_for(path):
    """Exact model version, saved next to the reply as <name>.model.txt (or MODEL.txt per folder)."""
    for candidate in (path.with_suffix(".model.txt"), path.parent / "MODEL.txt"):
        if candidate.exists():
            return candidate.read_text().strip()
    return "unrecorded"


def expected_sources(cards):
    from .generation import load_assignment
    assignment = load_assignment()["assignment"]
    expected = {c.scenario_id: assignment.get(c.scenario_id) for c in cards}
    for c in cards:
        if c.case_type == "natural":
            expected[c.scenario_id] = "natural"
    return expected


def run_import(cards):
    by_id = {c.scenario_id: c for c in cards}
    expected = expected_sources(cards)
    accepted, provenance, rejected, warnings = {}, {}, [], {}

    for source, path in raw_files():
        try:
            items = extract_json(path.read_text())
        except (ValueError, json.JSONDecodeError) as exc:
            rejected.append({"file": path.name, "conversation_id": None, "reason": f"unreadable: {exc}"})
            continue
        for item in items:
            cid = item.get("conversation_id") if isinstance(item, dict) else None
            conv, reason = check_item(item, by_id.get(cid), expected.get(cid), source)
            if reason:
                rejected.append({"file": path.name, "conversation_id": cid, "reason": reason})
                continue
            accepted[cid] = conv            # a later file (a redo) replaces an earlier one
            warnings.pop(cid, None)
            if walk_back_warning(item["turns"]):
                warnings[cid] = "a staff reply may take back an earlier statement"
            provenance[cid] = {"conversation_id": cid, "source_model": source,
                               "model_name": model_name_for(path), "file": path.name,
                               "set": "natural" if source == "natural" else "core"}

    # A rejection only matters if no other file supplied a valid version of that conversation.
    still_rejected = [r for r in rejected if r["conversation_id"] not in accepted]
    missing = sorted(set(by_id) - set(accepted))
    return accepted, provenance, still_rejected, missing, warnings


def write_outputs(accepted, provenance, rejected, missing, expected, warnings):
    CONVERSATIONS.parent.mkdir(parents=True, exist_ok=True)
    with CONVERSATIONS.open("w") as f:
        for cid in sorted(accepted):
            f.write(accepted[cid].model_dump_json() + "\n")
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    with PROVENANCE.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["conversation_id", "set", "source_model", "model_name", "file"])
        writer.writeheader()
        for cid in sorted(provenance):
            writer.writerow(provenance[cid])

    missing_by_source = defaultdict(list)
    for cid in missing:
        missing_by_source[expected[cid]].append(cid)
    report = {
        "accepted": len(accepted),
        "accepted_by_source": dict(sorted(Counter(p["source_model"] for p in provenance.values()).items())),
        "missing": len(missing),
        "missing_by_source": {k: sorted(v) for k, v in sorted(missing_by_source.items())},
        "rejected": rejected,
        "rejection_reasons": dict(Counter(r["reason"].split(":")[0] for r in rejected)),
        "warnings": dict(sorted(warnings.items())),
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    return report


def write_quality(accepted, provenance, cards):
    QUALITY.write_text(json.dumps(generation_quality(accepted, provenance, cards), indent=2) + "\n")


def attempted_ids():
    """Scenarios whose original batch has a saved reply. Batches not yet pasted are not redone:
    their prompts are still waiting in the main pack."""
    from .generation import load_assignment
    batches = load_assignment()["batches"]
    return {cid for name, ids in batches.items()
            if (RAW / name.split("_")[0] / f"{name}.json").exists() for cid in ids}


# Patient text that talks about the message itself rather than the person's life: the sign of a
# model acting out "write a long message" (found in ChatGPT's gen-v2 output, decision D31).
META_COMMENTARY = (r"I don'?t want to (send|guess|pretend|decide|spend|sound)|phrase the question|"
                   r"the (important|actual|main) (detail|bit|question)|in my (first )?message|"
                   r"I was trying to|I want to be clear")


def mistake_turn_words(card, turns):
    """Length of the staff turn that best matches the planted mistake's wording. A subtle
    mistake in a 30-word reply stands out; in a 70-word helpful reply it is easy to miss."""
    words = set(re.findall(r"[a-z']+", card.planted_failure.description.lower()))
    staff = [t.text for t in turns if t.speaker == "responder"]
    best = max(staff, key=lambda s: len(words & set(re.findall(r"[a-z']+", s.lower()))))
    return len(best.split())


def generation_quality(accepted, provenance, cards):
    """Per source model: simple measures of realism, so differences between generators are
    visible before any labelling (decision D31)."""
    by_id = {c.scenario_id: c for c in cards}
    groups = defaultdict(list)
    for cid, conv in accepted.items():
        groups[provenance[cid]["source_model"]].append((by_id[cid], conv))
    out = {}
    for source, items in sorted(groups.items()):
        turns = [len(conv.turns) for _, conv in items]
        rambling = [c for c, conv in items if c.persona.writing_style == "rambling"]
        meta = sum(bool(re.search(META_COMMENTARY, " ".join(t.text for t in conv.turns if t.speaker == "patient"), re.I))
                   for c, conv in items if c.persona.writing_style == "rambling")
        subtle = [mistake_turn_words(c, conv.turns) for c, conv in items
                  if c.planted_failure and c.planted_failure.difficulty == "subtle"]
        out[source] = {
            "conversations": len(items),
            "turns_mean": round(statistics.mean(turns), 2),
            "share_with_4_turns": round(sum(n == 4 for n in turns) / len(turns), 3),
            "rambling_patients": len(rambling),
            "rambling_with_meta_commentary": meta,
            "subtle_mistake_turn_words_median": statistics.median(subtle) if subtle else None,
        }
    return out


def write_redo_pack(cards):
    """Prompt files for every attempted scenario that is still missing, for its assigned model."""
    from .generation import BATCH_SIZE, PACK, build_prompt
    from .reference import facts, protocol_text

    _, _, _, missing, _ = run_import(cards)
    attempted = attempted_ids()
    missing = [cid for cid in missing if cid in attempted]
    expected = expected_sources(cards)
    by_id = {c.scenario_id: c for c in cards}
    redo_dir = PACK / "redo"
    redo_dir.mkdir(parents=True, exist_ok=True)
    for old in redo_dir.glob("*.md"):
        old.unlink()
    written = 0
    for source in ("chatgpt", "gemini", "claude"):
        ids = [cid for cid in missing if expected[cid] == source]
        for n, start in enumerate(range(0, len(ids), BATCH_SIZE), 1):
            batch = [by_id[i] for i in ids[start:start + BATCH_SIZE]]
            (redo_dir / f"{source}_redo_{n:02d}.md").write_text(build_prompt(batch, facts(), protocol_text()))
            written += 1
    print(f"{len(missing)} attempted scenario(s) still needed; wrote {written} redo prompt file(s) "
          "to generation/pack/redo/")


def main():
    cards = load_cards()
    accepted, provenance, rejected, missing, warnings = run_import(cards)
    report = write_outputs(accepted, provenance, rejected, missing, expected_sources(cards), warnings)
    write_quality(accepted, provenance, cards)
    print(json.dumps({k: report[k] for k in ("accepted", "accepted_by_source", "missing",
                                             "rejection_reasons", "warnings")}, indent=2))
    if missing:
        print("Run `python -m safety_eval.generation redo` to write prompts for the missing ones.")


if __name__ == "__main__":
    main()
