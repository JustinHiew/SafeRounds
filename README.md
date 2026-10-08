# Conversation safety eval harness

Can an automated LLM judge reliably flag patient-support conversations that are unsafe,
inaccurate, out of scope, ungrounded or missing an escalation, and where does it fail?

This repo builds that judge for a fictional online weight-management clinic and measures it
against human labels on a held-out test set.

> **Status:** under construction (milestone 1, step 1 of 9). No results yet.
> See `docs/decisions.md` for the build plan.

## Important caveats

- **Not medical advice.** Nothing in this repo is medical advice or a clinical guideline.
- **All data is synthetic.** Every conversation and patient record is invented. The clinic,
  Fernhill Weight Health, does not exist.
- **Labels come from one non-clinician.** The human standard the judge is measured against
  was produced by one person without clinical training.
- **Results on real conversations would likely be worse.** Real patients and staff write in
  ways a synthetic set does not capture.
- The judge is not safe to use on real patients, and this repo makes no claim that it is.

## Layout

```
data/reference/   reference sheet, clinic protocol, source register
data/             scenario cards, conversations, provenance, labels and splits
generation/       generation prompt pack and import script
prompts/          versioned judge prompts A–E
src/safety_eval/  model backends, cache, judge, checks, labelling tool, evaluation
service/          FastAPI app and SQLite log (milestone 2)
sql/              monitoring queries (milestone 3)
promptfoo/        dev comparison and regression configs
tests/            pytest suite
reports/          generated metrics and error tables
cache/            judge responses, committed so reports rebuild offline
docs/             problem, rubric, themes, decisions, concepts, results summary
```

## Setup

```bash
pip install -e ".[dev]"
pytest
```
