"""After blind labelling: compare labels with the scenario cards, then finalise (brief 8.4–8.6).

Commands:
  python -m safety_eval.reconcile report            list every disagreement with the cards
                                                     -> reports/reconcile.json, reports/plant_success.json
  python -m safety_eval.reconcile finalise          apply Justin's decisions -> data/labels/final_labels.jsonl
  python -m safety_eval.reconcile agreement pass2   agreement between pass1 and another pass
                                                     -> reports/agreement_pass2.json

Justin records one decision per disagreement in data/labels/reconcile_decisions.jsonl:
  {"conversation_id": "cv-...", "decision": "keep" | "relabel" | "ambiguous", "note": "..."}
  keep       the label stands (the card was wrong, or the generator did not plant the mistake)
  relabel    the label was a slip; relabel with `python -m safety_eval.labelling label --redo ID`
  ambiguous  excluded from headline metrics and reported separately
The labels, not the cards, are the ground truth.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from .grid import load_cards
from .labelling import LABELS, load_labels
from .metrics import cohen_kappa
from .schemas import CRITERIA

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
DECISIONS = LABELS / "reconcile_decisions.jsonl"
FINAL = LABELS / "final_labels.jsonl"
AMBIGUOUS = LABELS / "ambiguous.txt"


def sources():
    import csv
    return {r["conversation_id"]: r["source_model"]
            for r in csv.DictReader((ROOT / "data/provenance/source_models.csv").open())}


def disagreements(cards, labels):
    """Every labelled conversation whose label does not match what its card intended."""
    out = []
    for card in cards:
        lab = labels.get(card.scenario_id)
        if lab is None:
            continue
        reasons = []
        if card.planted_failure:
            crit = card.planted_failure.criterion
            if lab.verdicts[crit] == "pass":
                reasons.append(f"planted {crit} failure labelled pass")
            expected = {crit, *card.planted_failure.also_fails}
            extra = [c for c in CRITERIA if lab.verdicts[c] == "fail" and c not in expected]
            if extra:
                reasons.append(f"unexpected failure: {', '.join(extra)}")
            if lab.severity != card.expected_severity and lab.verdicts[crit] == "fail":
                reasons.append(f"severity {lab.severity}, card says {card.expected_severity}")
        elif lab.flagged:
            reasons.append(f"{card.case_type} case flagged: "
                           + ", ".join(c for c in CRITERIA if lab.verdicts[c] == "fail"))
        if lab.unsure:
            reasons.append("labeller unsure")
        if reasons:
            out.append({
                "conversation_id": card.scenario_id,
                "reasons": reasons,
                "card": {"case_type": card.case_type, "situation": card.situation,
                         "planted": card.planted_failure.model_dump() if card.planted_failure else None,
                         "correct_reply": card.correct_reply,
                         "expected_severity": card.expected_severity},
                "label": {"verdicts": lab.verdicts, "severity": lab.severity, "note": lab.note},
            })
    return out


def plant_success(cards, labels, source_of):
    """Share of failure cards where the label confirms the planted criterion failed (D22)."""
    groups = defaultdict(lambda: [0, 0])
    for card in cards:
        lab = labels.get(card.scenario_id)
        if not card.planted_failure or lab is None:
            continue
        hit = lab.verdicts[card.planted_failure.criterion] == "fail"
        for key in ("all", f"source:{source_of.get(card.scenario_id)}",
                    f"difficulty:{card.planted_failure.difficulty}",
                    f"criterion:{card.planted_failure.criterion}"):
            groups[key][0] += hit
            groups[key][1] += 1
    return {k: {"confirmed": h, "labelled": n, "share": round(h / n, 3)} for k, (h, n) in sorted(groups.items())}


def load_decisions():
    out = {}
    if DECISIONS.exists():
        for line in DECISIONS.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                if d["decision"] not in ("keep", "relabel", "ambiguous"):
                    raise ValueError(f"bad decision for {d['conversation_id']}: {d['decision']}")
                out[d["conversation_id"]] = d
    return out


def finalise(cards, labels, decisions):
    """Final labels = pass1 (with redos applied), minus conversations marked ambiguous.
    Refuses to run while any disagreement has no decision."""
    open_items = [d["conversation_id"] for d in disagreements(cards, labels)
                  if d["conversation_id"] not in decisions]
    if open_items:
        raise SystemExit(f"{len(open_items)} disagreement(s) still need a decision, e.g. {open_items[:3]}")
    ambiguous = sorted(cid for cid, d in decisions.items() if d["decision"] == "ambiguous")
    final = [labels[cid] for cid in sorted(labels) if cid not in ambiguous]
    FINAL.write_text("".join(lab.model_dump_json() + "\n" for lab in final))
    AMBIGUOUS.write_text("\n".join(ambiguous) + ("\n" if ambiguous else ""))
    return len(final), len(ambiguous)


def agreement(first, second):
    """How far two passes agree, per criterion and on the overall flag, on the conversations both labelled."""
    common = sorted(set(first) & set(second))
    if not common:
        raise SystemExit("no conversation is labelled in both passes")
    report = {"conversations": len(common), "criteria": {}}
    for crit in CRITERIA:
        a = [first[c].verdicts[crit] for c in common]
        b = [second[c].verdicts[crit] for c in common]
        report["criteria"][crit] = {"agreement": round(sum(x == y for x, y in zip(a, b)) / len(common), 3),
                                    "kappa": round(cohen_kappa(a, b), 3)}
    fa = [first[c].flagged for c in common]
    fb = [second[c].flagged for c in common]
    report["flag"] = {"agreement": round(sum(x == y for x, y in zip(fa, fb)) / len(common), 3),
                      "kappa": round(cohen_kappa(fa, fb), 3)}
    sa = [first[c].severity for c in common]
    sb = [second[c].severity for c in common]
    report["severity_agreement"] = round(sum(x == y for x, y in zip(sa, sb)) / len(common), 3)
    return report


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    cards = [c for c in load_cards() if c.case_type != "natural"]
    labels = load_labels("pass1")
    if cmd == "report":
        items = disagreements(cards, labels)
        (REPORTS / "reconcile.json").write_text(json.dumps(
            {"labelled": len(labels), "disagreements": len(items), "items": items}, indent=2) + "\n")
        (REPORTS / "plant_success.json").write_text(json.dumps(plant_success(cards, labels, sources()), indent=2) + "\n")
        print(f"{len(labels)} labelled, {len(items)} disagreement(s) -> reports/reconcile.json")
    elif cmd == "finalise":
        n, amb = finalise(cards, labels, load_decisions())
        print(f"{n} final labels, {amb} ambiguous")
    elif cmd == "agreement":
        other = sys.argv[2]
        report = agreement(labels, load_labels(other))
        (REPORTS / f"agreement_{other}.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
