"""promptfoo provider that replays saved judge results; it never calls a model.

Model calls go through `python -m safety_eval.run_judge`, which caches every call and asks before
large batches. promptfoo then only compares what was saved, so it cannot spend usage on its own.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safety_eval.compare import load_run  # noqa: E402
from safety_eval.run_judge import rel, run_path  # noqa: E402

_runs = {}


def call_api(prompt, options, context):
    cfg = options.get("config", {})
    path = run_path(cfg["version"], cfg.get("split", "dev"), cfg.get("model", "sonnet"), cfg.get("repeat", 0))
    if path not in _runs:
        if not path.exists():
            return {"error": f"no saved run at {rel(path)}; run safety_eval.run_judge first"}
        _runs[path] = load_run(path)
    result = _runs[path].get(context["vars"]["conversation_id"])
    if result is None:
        return {"error": "conversation missing from the saved run"}
    verdicts = {c: j.verdict for c, j in result.output.criteria.items()} if result.output else {}
    return {"output": json.dumps({"caught": result.routed_to_person, "outcome": result.outcome,
                                  "severity": result.output.severity if result.output else None,
                                  "verdicts": verdicts}),
            "cached": True, "metadata": {"model": result.model}}
