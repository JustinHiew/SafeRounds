"""Train / dev / test split of the 250 core conversations (decisions D19, D36).

- Grouped: every card from one situation family (or one blueprint, if it has no family) goes to
  the same split, so the judge is never tested on a near-copy of a prompt example.
- Stratified: groups are placed one at a time, each into the split that keeps the mix of
  (failure type or case type) x severity x source model closest to the target proportions.
- 10 / 30 / 60: train only supplies the few worked examples in the judge prompt; dev compares
  prompt versions; test is used once and gets the largest share because it carries the
  reported numbers (D36).

The natural-error set is never split: it is a separate held-out set (D23).

Run: python -m safety_eval.splits
"""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .grid import BLUEPRINTS, load_cards

ROOT = Path(__file__).resolve().parents[2]
SPLIT_DIR = ROOT / "data" / "labels" / "splits"
SUMMARY = ROOT / "reports" / "split_summary.json"
ASSIGNMENT = ROOT / "generation" / "assignment.json"
TARGETS = {"train": 0.10, "dev": 0.30, "test": 0.60}
SEED = 20261011


def family_of():
    """{blueprint_id: family name}; blueprints without a listed family are their own family."""
    families = yaml.safe_load(BLUEPRINTS.read_text()).get("families", {})
    return {bp: name for name, members in families.items() for bp in members}


def stratum(card, source):
    return (kind_of(card), card.expected_severity, source)


def kind_of(card):
    return card.planted_failure.criterion if card.planted_failure else card.case_type


def pick_train(groups, rng, per_kind=4, max_group=4):
    """Train only feeds worked examples into the judge prompt, so what matters is that it has
    some of every kind of case, not that it mirrors the strata. Small groups are drawn at random
    until every failure type, hard negatives and normal cases each have per_kind cards."""
    kinds = sorted({kind_of(c) for cards in groups.values() for c in cards})
    chosen, have = [], Counter()
    for kind in kinds:
        candidates = [g for g in sorted(groups) if g not in chosen and len(groups[g]) <= max_group
                      and any(kind_of(c) == kind for c in groups[g])]
        rng.shuffle(candidates)
        while have[kind] < per_kind and candidates:
            g = candidates.pop()
            chosen.append(g)
            have.update(kind_of(c) for c in groups[g])
    return chosen


def assign_groups(cards, sources, seed=SEED, targets=TARGETS):
    """{scenario_id: split}. Train is picked first (pick_train); the remaining groups go to dev
    or test, biggest first, each to the side whose stratum counts move closest to target
    (squared gap, scaled by target size so small strata count as much as big ones)."""
    rng = random.Random(seed)
    families = family_of()
    groups = defaultdict(list)
    for c in cards:
        groups[families.get(c.blueprint_id, c.blueprint_id)].append(c)
    out = {c.scenario_id: "train" for g in pick_train(groups, rng) for c in groups[g]}

    rest = [g for g in sorted(groups) if groups[g][0].scenario_id not in out]
    later = {s: f for s, f in targets.items() if s != "train"}
    share = {s: f / sum(later.values()) for s, f in later.items()}
    totals = Counter(stratum(c, sources[c.scenario_id]) for g in rest for c in groups[g])
    goal = {s: {k: share[s] * n for k, n in totals.items()} for s in later}
    have = {s: Counter() for s in later}

    rng.shuffle(rest)
    rest.sort(key=lambda g: -len(groups[g]))       # stable: ties keep the shuffled order
    for g in rest:
        add = Counter(stratum(c, sources[c.scenario_id]) for c in groups[g])

        def cost(split):
            return sum(((have[split][k] + n - goal[split][k]) ** 2 - (have[split][k] - goal[split][k]) ** 2)
                       / max(goal[split][k], 1) for k, n in add.items())

        best = min(later, key=lambda s: (cost(s), s))
        have[best].update(add)
        for c in groups[g]:
            out[c.scenario_id] = best
    return out


def summarise(cards, sources, split_of):
    by = defaultdict(lambda: defaultdict(Counter))
    groups = defaultdict(set)
    families = family_of()
    for c in cards:
        s = split_of[c.scenario_id]
        by[s]["kind"][kind_of(c)] += 1
        by[s]["source"][sources[c.scenario_id]] += 1
        by[s]["severity"][c.expected_severity] += 1
        groups[s].add(families.get(c.blueprint_id, c.blueprint_id))
    return {
        "seed": SEED,
        "targets": TARGETS,
        "splits": {s: {"cards": sum(by[s]["kind"].values()), "groups": len(groups[s]),
                       "by_kind": dict(sorted(by[s]["kind"].items())),
                       "by_source": dict(sorted(by[s]["source"].items())),
                       "by_severity": dict(sorted(by[s]["severity"].items()))}
                   for s in TARGETS},
    }


def load_splits():
    """{split: set of scenario IDs}, from the committed ID lists."""
    return {s: set((SPLIT_DIR / f"{s}.txt").read_text().split()) for s in TARGETS}


def main():
    cards = [c for c in load_cards() if c.case_type != "natural"]
    sources = json.loads(ASSIGNMENT.read_text())["assignment"]
    split_of = assign_groups(cards, sources)
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)
    for s in TARGETS:
        ids = sorted(cid for cid, sp in split_of.items() if sp == s)
        (SPLIT_DIR / f"{s}.txt").write_text("\n".join(ids) + "\n")
    summary = summarise(cards, sources, split_of)
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["splits"], indent=2))


if __name__ == "__main__":
    main()
