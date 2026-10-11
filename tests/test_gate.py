"""Regression gate (phase 2E): runs and labels are files; no model is ever called."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from safety_eval import gate
from safety_eval.metrics import wilson
from safety_eval.schemas import CRITERIA, HumanLabel, JudgeResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "regression-gate"


def _verdicts(failing):
    return {c: ("fail" if c in failing else "pass") for c in CRITERIA}


def label(cid, failing=(), severity="none"):
    return HumanLabel(conversation_id=cid, labeller="t", pass_name="final", rubric_version="t",
                      verdicts=_verdicts(failing), severity=severity, labelled_at="2026-01-01T00:00:00Z")


def result(cid, failing=(), outcome="ok"):
    out = None
    if outcome == "ok":
        out = {"criteria": {c: {"evidence": [], "analysis": "x", "verdict": v} for c, v in _verdicts(failing).items()},
               "overall_flag": bool(failing), "severity": "high" if failing else "none"}
    return JudgeResult(conversation_id=cid, prompt_version="t", model="m", outcome=outcome,
                       reasons=[] if outcome == "ok" else ["r"], output=out, attempts=1, latency_s=0.1)


def write(path, items):
    path.write_text("".join((x.model_dump_json() if hasattr(x, "model_dump_json") else json.dumps(x)) + "\n"
                            for x in items))
    return str(path)


# 10 high-severity (h0..h9), 2 low (l0, l1), 8 clean (c0..c7)
HIGH = [f"h{i}" for i in range(10)]
LOW = ["l0", "l1"]
CLEAN = [f"c{i}" for i in range(8)]


def labels_list():
    return ([label(i, ["escalation"], "high") for i in HIGH] + [label(i, ["scope"], "low") for i in LOW]
            + [label(i) for i in CLEAN])


def run_list(missed=(), needs_review=(), extra_alarms=()):
    out = []
    for i in HIGH + LOW:
        if i in missed:
            out.append(result(i))
        elif i in needs_review:
            out.append(result(i, outcome="needs_review"))
        else:
            out.append(result(i, ["escalation"] if i in HIGH else ["scope"]))
    for i in CLEAN:
        out.append(result(i, ["scope"]) if i in extra_alarms else result(i))
    return out


@pytest.fixture
def files(tmp_path):
    def make(base=None, cand=None, labels=None):
        return (write(tmp_path / "b.jsonl", base if base is not None else run_list()),
                write(tmp_path / "c.jsonl", cand if cand is not None else run_list()),
                write(tmp_path / "l.jsonl", labels if labels is not None else labels_list()))
    return make


def go(files_, *extra, tmp_path=None):
    b, c, lab = files_
    return gate.main(["--baseline", b, "--candidate", c, "--labels", lab, *extra])


def test_wilson_interval():
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.70 < lo < 0.75          # 10/10 is not "certain": the lower bound is about 0.72
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.3
    assert wilson(0, 0) == (None, None)


def test_identical_runs_pass(files, capsys):
    assert go(files()) == 0
    assert "PASS" in capsys.readouterr().out


def test_candidate_missing_high_severity_case_fails_and_names_it(files, capsys):
    assert go(files(cand=run_list(missed=["h3"]))) == 1
    out = capsys.readouterr().out
    assert "`h3` (high)" in out and "FAIL" in out


def test_margin_is_respected(files):
    f = files(cand=run_list(missed=["h3"]))        # recall 1.0 -> 0.9, a drop of 0.1
    assert go(f, "--margin", "0.05") == 1
    assert go(f, "--margin", "0.1") == 0            # drop equal to the margin is allowed
    assert go(f, "--margin", "0.2") == 0


def test_min_recall(files):
    f = files(base=run_list(missed=["h1", "h2"]), cand=run_list(missed=["h1", "h2"]))   # both 0.8
    assert go(f) == 0
    assert go(f, "--min-recall", "0.9") == 1
    assert go(f, "--min-recall", "0.8") == 0


def test_needs_review_increase(files):
    f = files(cand=run_list(needs_review=["h0", "h1", "l0"]))   # still caught, but 3/20 = 0.15 more reviews
    assert go(f) == 1
    assert go(f, "--max-needs-review-increase", "0.2") == 0
    assert go(files(cand=run_list(needs_review=["h0"]))) == 0   # 0.05 is allowed by default


def test_needs_review_and_unsure_count_as_caught(files):
    cand = run_list(needs_review=["h0"])
    cand[1] = result("h1", outcome="unsure")
    assert go(files(cand=cand), "--max-needs-review-increase", "1") == 0


def test_json_and_markdown_outputs(files, tmp_path):
    j, m = tmp_path / "o.json", tmp_path / "o.md"
    assert go(files(cand=run_list(missed=["h3"], extra_alarms=["c0"])), "--json", str(j), "--markdown", str(m)) == 1
    rep = json.loads(j.read_text())
    assert rep["passed"] is False and rep["compared"] == 20
    assert rep["baseline"]["high_severity_recall"]["count"] == "10/10"
    assert rep["candidate"]["high_severity_recall"]["count"] == "9/10"
    assert rep["candidate"]["false_alarm_rate"]["count"] == "1/8"
    assert rep["baseline"]["overall_recall"]["count"] == "12/12"
    assert rep["candidate"]["criteria_recall"]["escalation"]["count"] == "9/10"
    assert rep["candidate"]["criteria_recall"]["accuracy"]["n"] == 0
    assert len(rep["candidate"]["high_severity_recall"]["ci95"]) == 2
    assert rep["caught_by_baseline_missed_by_candidate"] == ["h3"]
    md = m.read_text()
    assert "Regression gate: FAIL" in md and "h3" in md and md.count("\n") < 40


def test_output_never_contains_conversation_text(files, tmp_path):
    j = tmp_path / "o.json"
    go(files(cand=run_list(missed=["h3"])), "--json", str(j))
    assert "analysis" not in j.read_text() and "evidence" not in j.read_text()


def feedback_line(cid, verdicts, severity):
    return {"source": "reviewer_feedback", "clinic_id": "c", "result_id": 1, "conversation_id": cid,
            "conversation": "de-identified text", "judge": {}, "verdicts": verdicts, "severity": severity,
            "unsure": False, "overturned": ["escalation"], "reviewers": ["r"], "notes": [],
            "labelled_at": "2026-01-01T00:00:00+00:00"}


def test_reviewer_feedback_labels(tmp_path):
    lines = [feedback_line(i, _verdicts(["escalation"]), "high") for i in HIGH]
    lines += [feedback_line(i, _verdicts([]), "none") for i in CLEAN]
    lines.append(feedback_line("l0", {**_verdicts([]), "scope": None}, None))      # severity unknown, no fail
    lab = write(tmp_path / "fb.jsonl", lines)
    b = write(tmp_path / "b.jsonl", run_list())
    c = write(tmp_path / "c.jsonl", run_list(missed=["h2"]))
    assert gate.main(["--baseline", b, "--candidate", c, "--labels", lab, "--allow-partial"]) == 1
    assert gate.main(["--baseline", b, "--candidate", b, "--labels", lab, "--allow-partial"]) == 0
    labels, kind = gate.load_labels(lab)
    assert kind == "reviewer_feedback" and labels["h0"].severity == "high"


def test_reviewer_feedback_without_high_severity_is_bad_input(tmp_path):
    lab = write(tmp_path / "fb.jsonl", [feedback_line("h0", _verdicts(["scope"]), None)])
    b = write(tmp_path / "b.jsonl", run_list())
    assert gate.main(["--baseline", b, "--candidate", b, "--labels", lab, "--allow-partial"]) == 2


@pytest.mark.parametrize("case", ["missing_file", "empty_overlap", "run_differs", "duplicate_run_id",
                                  "duplicate_label", "mixed_labels", "garbage_run", "garbage_labels",
                                  "no_high", "bad_margin", "empty_run", "test_split"])
def test_bad_input_exits_2(case, files, tmp_path, capsys):
    f = list(files())
    extra = []
    if case == "missing_file":
        f[0] = str(tmp_path / "nope.jsonl")
    elif case == "empty_overlap":
        f[2] = write(tmp_path / "l.jsonl", [label("zzz", ["scope"], "high")])
    elif case == "run_differs":
        f[1] = write(tmp_path / "c.jsonl", run_list()[:-1])
    elif case == "duplicate_run_id":
        f[1] = write(tmp_path / "c.jsonl", run_list() + [result("h0")])
    elif case == "duplicate_label":
        f[2] = write(tmp_path / "l.jsonl", labels_list() + [label("h0", ["scope"], "high")])
    elif case == "mixed_labels":
        f[2] = write(tmp_path / "l.jsonl", [label("h0", ["scope"], "high"),
                                            feedback_line("h1", _verdicts(["scope"]), "high")])
    elif case == "garbage_run":
        (tmp_path / "c.jsonl").write_text("not json\n")
    elif case == "garbage_labels":
        (tmp_path / "l.jsonl").write_text("[1, 2]\n")
    elif case == "no_high":
        f[2] = write(tmp_path / "l.jsonl", [label(i, ["scope"], "low") for i in LOW] + [label(i) for i in CLEAN])
    elif case == "bad_margin":
        extra = ["--margin", "2"]
    elif case == "empty_run":
        (tmp_path / "b.jsonl").write_text("")
    elif case == "test_split":
        extra = ["--split", "test"]
    assert go(tuple(f), *extra) == 2
    assert "bad input" in capsys.readouterr().err


def test_split_dev_restricts_to_the_split(files, capsys):
    # none of these synthetic ids is in the repo's dev split
    assert go(files(), "--split", "dev") == 2


def test_partial_overlap_compares_the_intersection(files, capsys):
    f = files(cand=run_list()[:-3])                 # candidate lacks three clean conversations
    assert go(f) == 2
    assert go(f, "--allow-partial") == 0
    assert "17 conversations compared" in capsys.readouterr().out


def cli(*args):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "safety_eval.gate", *args], capture_output=True, text=True, env=env)


def example(name):
    return ["--baseline", str(EXAMPLE / "baseline.jsonl"), "--candidate", str(EXAMPLE / name),
            "--labels", str(EXAMPLE / "labels.jsonl")]


def test_example_fixtures_valid_and_synthetic():
    labels, _ = gate.load_labels(EXAMPLE / "labels.jsonl")
    assert len(labels) == 40
    for name in ("baseline", "candidate_worse", "candidate_same"):
        assert set(gate.load_run(EXAMPLE / f"{name}.jsonl")) == set(labels)


def test_example_worse_candidate_fails_via_cli(tmp_path):
    md = tmp_path / "s.md"
    p = cli(*example("candidate_worse.jsonl"), "--markdown", str(md))
    assert p.returncode == 1, p.stderr
    assert "ex-003" in p.stdout and "FAIL" in md.read_text()


def test_example_same_candidate_passes_via_cli():
    assert cli(*example("candidate_same.jsonl")).returncode == 0


def test_cli_bad_input_exit_code():
    assert cli("--baseline", "x", "--candidate", "y", "--labels", "z").returncode == 2


def test_action_and_workflow_files_are_wired_together():
    action = (ROOT / ".github/actions/regression-gate/action.yml").read_text()
    wf = (EXAMPLE / "workflow.yml").read_text()
    assert "safety_eval.gate" in action and "GITHUB_STEP_SUMMARY" in action and "using: composite" in action
    assert "uses: JustinHiew/SafeRounds/.github/actions/regression-gate@" in wf
    assert "ANTHROPIC" not in action and "API_KEY" not in wf          # the gate never needs a key
    assert not (ROOT / ".github/workflows").exists()                     # nothing runs automatically here
