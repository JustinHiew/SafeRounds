"""Run a judge prompt version over a split and save the results (brief 10.4, 11).

  python -m safety_eval.run_judge --version C --split dev
  python -m safety_eval.run_judge --version A --split dev                      # rules, no model
  python -m safety_eval.run_judge --version D --split test                     # only after choosing D
  python -m safety_eval.run_judge --version D --split test --repeat 1          # consistency re-runs

Results go to data/judge_runs/<version>__<split>__<model>__r<repeat>.jsonl, one JudgeResult per
line (verdicts, evidence quotes and analysis, never the conversation). Model calls are cached;
the number of new calls is printed first and anything over 100 needs a yes.

The test set is protected (brief 10.4): it runs only for baseline A and the version recorded in
data/labels/test_run_authorisation.json, which Justin writes when he chooses the final version on
dev. Running test also appends a dated line to docs/decisions.md, so when it happened is on record.
"""

import argparse
import datetime as dt
import json
from pathlib import Path

from .baseline_rules import judge_rules
from .cache import ResponseCache, confirm_batch
from .judge import EXAMPLES, PromptVersion, judge, needed_calls
from .labelling import load_conversations
from .llm import CliBackend
from .splits import load_splits

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "data" / "judge_runs"
AUTH = ROOT / "data" / "labels" / "test_run_authorisation.json"
DECISIONS = ROOT / "docs" / "decisions.md"
DEFAULT_MODEL = "sonnet"   # decision D40


def rel(path):
    """Path relative to the repo for messages; unchanged if it lies elsewhere (tests)."""
    try:
        return Path(path).relative_to(ROOT)
    except ValueError:
        return path


def run_path(version, split, model, repeat):
    return RUNS / f"{version}__{split}__{model}__r{repeat}.jsonl"


def check_test_allowed(version):
    if version == "A":
        return
    if not AUTH.exists():
        raise SystemExit("The test set is locked until a version is chosen on dev. Record the choice in "
                         f"{rel(AUTH)} as {{\"chosen_version\": \"D\", \"date\": \"...\", \"reason\": \"...\"}}.")
    chosen = json.loads(AUTH.read_text())["chosen_version"]
    if version != chosen:
        raise SystemExit(f"Only baseline A and the chosen version ({chosen}) may run on test.")


def examples_for(prompt):
    return EXAMPLES.read_text() if "{{EXAMPLES}}" in prompt.template and EXAMPLES.exists() else ""


def run(version, split, model=DEFAULT_MODEL, repeat=0, backend=None, assume_yes=False, convs=None):
    ids = load_splits()[split]
    convs = [c for c in (convs or load_conversations()) if c.conversation_id in ids]
    if split == "test":
        check_test_allowed(version)

    if version == "A":
        results = [judge_rules(c) for c in convs]
        model = "rules"
    else:
        prompt = PromptVersion.load(version)
        if "{{EXAMPLES}}" in prompt.template and not EXAMPLES.exists():
            raise SystemExit("prompts/examples.md is missing: build it with `python -m safety_eval.examples`.")
        backend = backend or CliBackend(model=model)
        cache = ResponseCache("judge")
        new = sum(needed_calls(prompt, c, backend, cache, repeat) for c in convs)
        if not confirm_batch(new, assume_yes=assume_yes):
            return None
        examples = examples_for(prompt)
        results = []
        for i, c in enumerate(convs, 1):
            results.append(judge(c, prompt, backend, cache, examples=examples, repeat=repeat))
            if i % 10 == 0:
                print(f"  {i}/{len(convs)}")

    RUNS.mkdir(parents=True, exist_ok=True)
    out = run_path(version, split, model, repeat)
    out.write_text("".join(r.model_dump_json() + "\n" for r in results))
    if split == "test":
        with DECISIONS.open("a") as f:
            f.write(f"\n- Test-set run recorded: version {version}, model {model}, repeat {repeat}, "
                    f"{dt.date.today().isoformat()} ({len(results)} conversations).\n")
    print(f"wrote {len(results)} results to {rel(out)}")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True)
    p.add_argument("--split", choices=["train", "dev", "test"], required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--yes", action="store_true")
    a = p.parse_args()
    run(a.version, a.split, a.model, a.repeat, assume_yes=a.yes)


if __name__ == "__main__":
    main()
