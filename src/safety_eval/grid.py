"""Expand the hand-written blueprints into the scenario grid (data/scenarios.yaml).

Each failure blueprint becomes two cards (obvious and subtle); each hard-negative, normal and
natural blueprint becomes two cards with different personas and records. Responder type and persona
traits are assigned greedily so they stay balanced within each failure type, which stops a
judge from looking good or bad just because, say, most Scope failures came from support agents.

Everything random comes from one seeded generator, so rerunning gives the same grid.

Run: python -m safety_eval.grid
"""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .schemas import (
    AGE_BANDS,
    ANXIETY_LEVELS,
    CRITERIA,
    PRESENTATIONS,
    WRITING_STYLES,
    PatientRecord,
    Persona,
    PlantedFailure,
    ScenarioCard,
)

ROOT = Path(__file__).resolve().parents[2]
BLUEPRINTS = ROOT / "data" / "scenario_blueprints.yaml"
SCENARIOS = ROOT / "data" / "scenarios.yaml"
SUMMARY = ROOT / "reports" / "scenario_grid_summary.json"
SEED = 20261008

# Background conditions for records whose blueprint does not set any. Kept to common, stable
# conditions so a random record never creates a red flag the blueprint did not intend.
BACKGROUND = [
    ("hypertension", ["perindopril"]),
    ("hypertension", ["amlodipine"]),
    ("high cholesterol", ["atorvastatin"]),
    ("obstructive sleep apnoea", []),
    ("osteoarthritis", ["paracetamol as needed"]),
    ("asthma", ["salbutamol inhaler as needed"]),
    ("type 2 diabetes", ["metformin"]),
    ("hypothyroidism", ["levothyroxine"]),
]
NAMES = {
    "female": ["Aisha", "Bec", "Chloe", "Daniela", "Emma", "Fatima", "Grace", "Hannah", "Isla",
               "Jess", "Kiri", "Leah", "Mai", "Nadia", "Olivia", "Priya", "Rosa", "Sarah",
               "Tash", "Uma", "Vanessa", "Wen", "Yasmin", "Zoe", "Mei", "Anh", "Sofia",
               "Leila", "Ruby", "Ngaire"],
    "male": ["Adam", "Ben", "Chris", "Dev", "Eli", "Farid", "George", "Hamish", "Ivan",
             "Jack", "Kai", "Liam", "Marco", "Nathan", "Omar", "Paul", "Quang", "Raj",
             "Sam", "Tom", "Vikram", "Will", "Yusuf", "Zac", "Hiro", "Luca", "Mateo",
             "Rangi", "Darius", "Josh"],
}


def load_blueprints(path=BLUEPRINTS):
    data = yaml.safe_load(path.read_text())
    default_responders = data["defaults"]["responders"]
    out = []
    for kind in ("failure", "hard_negative", "normal", "natural"):
        for bp in data[kind]:
            bp = {"responders": default_responders, "record": {}, "persona": {}, "tier": "none",
                  "sensitive": False, **bp, "kind": kind}
            out.append(bp)
    ids = [bp["id"] for bp in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate blueprint IDs")
    return out


def card_specs(blueprints):
    """Two cards per blueprint: obvious/subtle for failures, two independent draws otherwise."""
    for bp in blueprints:
        if bp["kind"] == "failure":
            yield bp, "obvious"
            yield bp, "subtle"
        else:
            yield bp, None
            yield bp, None


def group_of(bp):
    # Balancing happens within each failure type, and within each no-failure type.
    return bp["criterion"] if bp["kind"] == "failure" else bp["kind"]


def least_used(options, counts, rng):
    """Pick the option used least so far in this group; break ties at random."""
    low = min(counts[o] for o in options)
    choice = rng.choice([o for o in options if counts[o] == low])
    counts[choice] += 1
    return choice


def weeks_for_step(step):
    # Doses usually step up every 4 weeks (F03); step 5 is the maintenance dose.
    return (17, 40) if step == 5 else ((step - 1) * 4 + 1, step * 4)


def step_for_weeks(weeks):
    return min(5, (weeks - 1) // 4 + 1)


def sample_record(constraints, rng):
    c = constraints
    age_band = c.get("age_band") or rng.choice(AGE_BANDS)
    sex = c.get("sex") or rng.choice(["female", "male"])
    if "pregnancy_status" in c:
        pregnancy = c["pregnancy_status"]
    else:
        pregnancy = "not_applicable" if sex == "male" else "not_pregnant"

    if "dose_step" in c:
        step = c["dose_step"]
        lo, hi = c.get("weeks") or weeks_for_step(step)
        weeks = rng.randint(lo, hi)
    else:
        lo, hi = c.get("weeks") or (1, 40)
        weeks = rng.randint(lo, hi)
        step = step_for_weeks(weeks)

    if "conditions" in c or c.get("no_conditions"):
        conditions = list(c.get("conditions", []))
        medications = list(c.get("other_medications", []))
    elif rng.random() < 0.5:
        conditions, medications = [], []
    else:
        condition, meds = rng.choice(BACKGROUND)
        conditions, medications = [condition], list(meds)

    return PatientRecord(age_band=age_band, sex=sex, dose_step=step, weeks_on_treatment=weeks,
                         conditions=conditions, pregnancy_status=pregnancy,
                         other_medications=medications)


def build_cards(blueprints, seed=SEED):
    rng = random.Random(seed)
    responder_counts = defaultdict(Counter)
    trait_counts = defaultdict(lambda: defaultdict(Counter))
    unused_names = {sex: rng.sample(names, len(names)) for sex, names in NAMES.items()}
    used_ids = set()
    cards = []

    for bp, difficulty in card_specs(blueprints):
        group = group_of(bp)
        responder = least_used(bp["responders"], responder_counts[group], rng)
        record = sample_record(bp["record"], rng)

        traits = {}
        for trait, options in (("writing_style", WRITING_STYLES), ("presentation", PRESENTATIONS),
                               ("anxiety", ANXIETY_LEVELS)):
            fixed = bp["persona"].get(trait)
            traits[trait] = least_used([fixed] if fixed else list(options),
                                       trait_counts[group][trait], rng)
        if not unused_names[record.sex]:
            unused_names[record.sex] = rng.sample(NAMES[record.sex], len(NAMES[record.sex]))
        persona = Persona(first_name=unused_names[record.sex].pop(), **traits)

        # Opaque IDs: nothing about the scenario can be read from the ID or its order.
        scenario_id = f"cv-{rng.getrandbits(24):06x}"
        while scenario_id in used_ids:
            scenario_id = f"cv-{rng.getrandbits(24):06x}"
        used_ids.add(scenario_id)

        failure = None
        if bp["kind"] == "failure":
            failure = PlantedFailure(criterion=bp["criterion"], difficulty=difficulty,
                                     description=bp[difficulty], also_fails=bp.get("also_fails", []))
        cards.append(ScenarioCard(
            scenario_id=scenario_id,
            blueprint_id=bp["id"],
            case_type=bp["kind"],
            theme_id=bp["theme"],
            responder=responder,
            persona=persona,
            patient_record=record,
            situation=bp["situation"],
            escalation_tier=bp["tier"],
            expected_severity=None if bp["kind"] == "natural" else bp.get("severity", "none"),
            planted_failure=failure,
            correct_reply=bp.get("correct"),
            facts=bp["facts"],
            sensitive=bp["sensitive"],
        ))
    return sorted(cards, key=lambda c: c.scenario_id)


def summarise(cards):
    """Counts that show the grid is balanced. Written to reports/ so docs can cite them."""
    def count(key):
        return dict(sorted(Counter(key(c) for c in cards).items()))

    failures = [c for c in cards if c.planted_failure]
    by_criterion = defaultdict(list)
    for c in failures:
        by_criterion[c.planted_failure.criterion].append(c)

    per_criterion = {}
    for crit in CRITERIA:
        group = by_criterion[crit]
        per_criterion[crit] = {
            "cards": len(group),
            "difficulty": dict(Counter(c.planted_failure.difficulty for c in group)),
            "severity": dict(Counter(c.expected_severity for c in group)),
            "responder": dict(Counter(c.responder for c in group)),
            "themes": len({c.theme_id for c in group}),
        }
    core = [c for c in cards if c.case_type != "natural"]
    return {
        "seed": SEED,
        "total_cards": len(cards),
        "core_cards": len(core),
        "natural_cards": len(cards) - len(core),
        "case_type": count(lambda c: c.case_type),
        "failures_by_criterion": per_criterion,
        "responder": count(lambda c: c.responder),
        "theme": count(lambda c: c.theme_id),
        "writing_style": count(lambda c: c.persona.writing_style),
        "presentation": count(lambda c: c.persona.presentation),
        "anxiety": count(lambda c: c.persona.anxiety),
        "escalation_tier": count(lambda c: c.escalation_tier),
        "high_severity_cards": sum(c.expected_severity == "high" for c in core),
        "sensitive_cards": sum(c.sensitive for c in cards),
    }


def write(cards):
    header = (
        "# Scenario cards: the intended answer for each conversation.\n"
        "# GENERATED by `python -m safety_eval.grid` from data/scenario_blueprints.yaml. Do not edit by hand.\n"
        "# The judge must never see this file.\n"
    )
    body = yaml.safe_dump([c.model_dump() for c in cards], sort_keys=False, allow_unicode=True, width=100)
    SCENARIOS.write_text(header + body)
    SUMMARY.write_text(json.dumps(summarise(cards), indent=2) + "\n")


def load_cards(path=SCENARIOS):
    return [ScenarioCard(**c) for c in yaml.safe_load(path.read_text())]


def main():
    cards = build_cards(load_blueprints())
    write(cards)
    print(json.dumps(summarise(cards), indent=2))


if __name__ == "__main__":
    main()
