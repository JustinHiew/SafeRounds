"""External check against physician labels: the HealthBench meta-evaluation (decision D35).

HealthBench (Arora et al. 2025, arXiv:2505.08775; code and data MIT-licensed at
github.com/openai/simple-evals) publishes model replies to health conversations, each graded by
2 to 5 physicians on whether it meets a criterion. The "emergency behaviour" criterion asks
whether the reply referred the user to care with the right urgency, which is close to this
project's Escalation criterion.

This module:
  1. downloads the file once (pinned URL and checksum) to data/external/, which is git-ignored:
     the data carries a canary string asking that it not be redistributed into training corpora;
  2. measures how often physicians agree with each other, the ceiling for any judge;
  3. draws a seeded, balanced sample for the judge to grade in step 8, committing only IDs.

Run: python -m safety_eval.external
"""

import hashlib
import json
import random
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from .metrics import pooled_pairwise

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "external" / "healthbench_meta_eval.jsonl"
SAMPLE = ROOT / "data" / "external_ids" / "healthbench_sample.json"
REPORT = ROOT / "reports" / "healthbench_physician_agreement.json"
URL = ("https://openaipublic.blob.core.windows.net/simple-evals/healthbench/"
       "2025-05-07-06-14-12_oss_meta_eval.jsonl")
SHA256 = "6a343ed3fcb6f28353c2c6c157ae30ddf2db874c947b27ba474ad54da9c5dbac"
SEED = 20261010
PER_CELL = 25

# Emergencies and context-dependent cases: there, "met" means the reply escalated with enough
# urgency. Non-emergent cases are excluded from the judge comparison because physicians mark
# over-escalation as a failure there, while the clinic protocol does not (D35); they are still
# included in the physician-agreement report.
CATEGORIES = {
    "emergent": "cluster:emergency_referrals_emergent_emergency_behavior",
    "conditionally_emergent": "cluster:emergency_referrals_conditionally-emergent_emergency_behavior",
    "non_emergent": "cluster:emergency_referrals_non-emergent_emergency_behavior",
}
JUDGED = ("emergent", "conditionally_emergent")


def download():
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL, RAW)
    digest = hashlib.sha256(RAW.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError(f"{RAW} checksum {digest} does not match the pinned version")


def load_emergency_rows():
    """{short category name: [rows]} for the three emergency-behaviour categories."""
    wanted = {v: k for k, v in CATEGORIES.items()}
    out = defaultdict(list)
    with RAW.open() as f:
        for line in f:
            row = json.loads(line)
            if row["category"] in wanted:
                out[wanted[row["category"]]].append(row)
    return out


def physician_agreement(rows_by_cat):
    report = {}
    for cat, rows in rows_by_cat.items():
        agree, kappa, pairs = pooled_pairwise([r["binary_labels"] for r in rows])
        unanimous = [r for r in rows if len(set(r["binary_labels"])) == 1]
        report[cat] = {
            "graded_replies": len(rows),
            "physician_pairs": pairs,
            "pairwise_agreement": round(agree, 3),
            "pairwise_kappa": round(kappa, 3),
            "share_unanimous": round(len(unanimous) / len(rows), 3),
            "unanimous_met": sum(r["binary_labels"][0] for r in unanimous),
            "unanimous_not_met": sum(not r["binary_labels"][0] for r in unanimous),
        }
    return report


def draw_sample(rows_by_cat, seed=SEED, per_cell=PER_CELL):
    """Balanced sample: per judged category, per_cell replies physicians unanimously marked as
    met and per_cell unanimously marked as not met. Unanimous rows only, so the reference label
    is not itself in dispute; one reply per conversation, so no conversation counts twice."""
    rng = random.Random(seed)
    sample = []
    for cat in JUDGED:
        for label in (True, False):
            pool = [r for r in rows_by_cat[cat]
                    if len(set(r["binary_labels"])) == 1 and r["binary_labels"][0] is label]
            pool.sort(key=lambda r: r["completion_id"])
            rng.shuffle(pool)
            seen, picked = set(), []
            for r in pool:
                if r["prompt_id"] not in seen and r["prompt_id"] not in {s["prompt_id"] for s in sample}:
                    seen.add(r["prompt_id"])
                    picked.append({"completion_id": r["completion_id"], "prompt_id": r["prompt_id"],
                                   "category": cat, "physicians_say_met": label,
                                   "n_physicians": len(r["binary_labels"])})
                if len(picked) == per_cell:
                    break
            sample += picked
    return sample


def main():
    download()
    rows = load_emergency_rows()
    report = {
        "source": "HealthBench meta-evaluation (Arora et al. 2025), MIT-licensed",
        "source_url": URL,
        "source_sha256": SHA256,
        "method": "Pooled pairwise agreement and kappa over all physician pairs on the same reply; "
                  "see src/safety_eval/metrics.py",
        "by_category": physician_agreement(rows),
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    sample = draw_sample(rows)
    SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE.write_text(json.dumps({"seed": SEED, "per_cell": PER_CELL, "items": sample}, indent=2) + "\n")
    print(json.dumps(report["by_category"], indent=2))
    print(f"sample: {len(sample)} replies, {Counter((s['category'], s['physicians_say_met']) for s in sample)}")


if __name__ == "__main__":
    main()
