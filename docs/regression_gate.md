# Regression gate (product phase 2E)

A change to a judge prompt or model can quietly make the judge miss cases it used to catch. The gate
compares two saved judge runs against human labels and fails when the new one catches fewer
high-severity cases. It reads files only: no model call, no API key, nothing written to `data/labels/`.

```
PYTHONPATH=src python -m safety_eval.gate \
    --baseline judge/baseline.jsonl --candidate judge/candidate.jsonl --labels judge/labels.jsonl \
    [--margin 0.02] [--min-recall 0.90] [--max-needs-review-increase 0.05] \
    [--allow-partial] [--split dev] [--json out.json] [--markdown out.md]
```

## Inputs

- **Runs**: `JudgeResult` JSONL, the format `safety_eval.run_judge` writes to `data/judge_runs/`
  (verdicts and evidence quotes, never the conversation).
- **Labels**, one file, one kind:
  - `HumanLabel` JSONL (for example `data/labels/final_labels.jsonl`): one reconciled label per
    conversation. Several passes for one conversation are refused.
  - The **reviewer-feedback export** from the service (lines with `"source": "reviewer_feedback"`,
    see `docs/review_queue.md`). These are only results a reviewer overturned, so they test the
    cases the judge once got wrong; they are not a representative sample. A line whose severity is
    `null` (a reviewer flipped a criterion to fail and the judge gave no severity) is not counted as
    high severity. Mixing the two kinds in one file is refused.

## What it checks

Conversations compared: those labelled **and** present in both runs. "Caught" is the D37
definition: flagged, unsure or needs_review.

| Check | Fails when |
|---|---|
| High-severity recall vs baseline | candidate recall < baseline recall - `--margin` (default 0.02; a drop equal to the margin passes) |
| `--min-recall` (optional) | candidate high-severity recall < the floor |
| needs_review | candidate needs_review rate is more than `--max-needs-review-increase` (default 0.05, share of conversations) above the baseline's |

Exit codes: **0** pass, **1** regression, **2** bad input. Exit 2 covers a missing or empty file,
invalid lines, duplicate ids, mixed label kinds, an empty overlap, runs that cover different
labelled conversations (unless `--allow-partial`), and no high-severity case in the overlap (recall
would be undefined). The gate never passes by comparing nothing.

## Reading the output

The report shows, for baseline and candidate, with 95% Wilson intervals and the raw counts:
high-severity recall (the headline), overall recall on human-flagged cases, the false-alarm rate
(share of human-unflagged cases the judge sent to a person), the needs_review rate, and recall per
criterion (judge verdict `fail` against a human `fail`; criteria with no failing case show 0/0).

Then every conversation **caught by the baseline and missed by the candidate**, by id only. Ids
marked `(high)` are the ones that matter most; ids marked `(no human flag ...)` are baseline false
alarms the candidate no longer raises, which is an improvement. Open those conversations in your
own system; the report never contains conversation text.

`--json` writes everything above (for dashboards or a later check); `--markdown` writes a short
table for a CI comment or job summary.

## Using it in CI

`.github/actions/regression-gate/action.yml` is a composite action: it sets up Python, installs this
package, runs the gate, appends the markdown to the job summary and fails the job on exit 1 or 2.
`examples/regression-gate/workflow.yml` shows a customer wiring it to a pull request that changes
prompts. The customer's own step produces `candidate.jsonl` for the same conversations as the
committed `baseline.jsonl`; refresh the baseline deliberately when a change is accepted.
This repository has no workflow that runs the gate automatically.

Try it locally on the synthetic fixtures (the worse candidate misses three high-severity cases):

```
PYTHONPATH=src python -m safety_eval.gate --baseline examples/regression-gate/baseline.jsonl \
    --candidate examples/regression-gate/candidate_worse.jsonl --labels examples/regression-gate/labels.jsonl
echo $?   # 1
```

## Limits

- **Small label sets give wide intervals.** With 12 high-severity cases, 12/12 has a lower bound of
  about 76%. The gate compares point estimates against the margin; it does not test significance, and
  warns when there are fewer than 20 high-severity cases. A passing gate means "no detected
  regression", not "no regression". The margin of 0.02 is only meaningful with hundreds of cases;
  with a dozen, one miss is a drop of about 0.08 and always trips it.
- It measures the labelled set only: it says nothing about conversations unlike those.
- The `--split` option applies to this repository's research splits; `test` is refused.
- Runs from different clinics' labels, or a baseline judged on other conversations, are refused
  rather than silently reduced to the overlap.
