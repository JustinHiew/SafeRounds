"""Generate one promptfoo test per labelled conversation in a split (default: dev)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safety_eval.examples import final_labels  # noqa: E402
from safety_eval.splits import load_splits  # noqa: E402


def labelled_tests(config=None):
    split = (config or {}).get("split", "dev")
    labels = final_labels()
    if os.environ.get("SAFETY_EVAL_LABELS"):          # only for the plumbing test in tests/
        import json
        from safety_eval.schemas import HumanLabel
        labels = {l["conversation_id"]: HumanLabel(**l) for l in
                  map(json.loads, Path(os.environ["SAFETY_EVAL_LABELS"]).read_text().splitlines())}
    ids = sorted(i for i in load_splits()[split] if i in labels)
    if not ids:
        raise RuntimeError("no final labels for this split yet")
    return [{"description": cid,
             "vars": {"conversation_id": cid, "human_flagged": labels[cid].flagged,
                      "human_severity": labels[cid].severity},
             "assert": [{"type": "python", "value": "file://assert_label.py"}]}
            for cid in ids]
