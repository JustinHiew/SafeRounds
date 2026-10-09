# Conversation safety eval harness

Can an automated LLM judge reliably flag patient-support conversations that are unsafe,
inaccurate, out of scope, ungrounded or missing an escalation, and where does it fail?

This repo builds that judge for a fictional Australian online weight-management clinic, where
patients message an AI assistant, a health coach or a patient support agent, and measures it
against human labels on a held-out test set.

> **Status:** under construction (milestone 1). Data generation, splits, labelling tool and judge
> are built; human labelling and judge runs have not started. No results yet.
> See `docs/decisions.md` for the build plan and `docs/datasheet.md` for the evaluation sets.

## Important caveats

- **Not medical advice.** Nothing in this repo is medical advice or a clinical guideline.
- **All data is synthetic.** Every conversation and patient record is invented. The clinic,
  Fernhill Weight Health, does not exist.
- **Labels come from one non-clinician.** The human standard the judge is measured against
  was produced by one person without clinical training.
- **Results on real conversations would likely be worse.** Real patients and staff write in
  ways a synthetic set does not capture.
- The judge is not safe to use on real patients, and this repo makes no claim that it is.

## Mapping to a production eval layer

The project is a small, offline version of the evaluation and monitoring layer a digital
health clinic needs for patient-facing conversations. Each piece has a production counterpart:

| In this repo | In production |
|---|---|
| Five criteria and a frozen, versioned rubric | A shared definition of "what good looks like", agreed with clinicians, coaches and support leads |
| Reference sheet, clinic protocol and patient record given to the judge | The judge reads the clinic's approved content and the patient's record, so it can check replies against real context |
| Human labels, self-consistency check, held-out test set | A labelled eval set with a known ceiling on how reliable the human standard is |
| Prompt versions A–E compared on a dev set | Changes to the judge are chosen on evidence, not intuition |
| Recall on high-severity cases as the headline, with intervals | Thresholds set from the cost of a missed harm versus a false alarm |
| "Unsure" and "needs review" routed to a person, failures fail closed | The judge never silently passes something it could not assess |
| `POST /evaluate`, SQLite log, SQL monitoring queries with alert bands *(milestones 2–3)* | A service that flags risky conversations for review and alerts when rates drift |
| promptfoo regression gate *(milestone 2)* | No prompt or model change ships if safety recall drops |
| `POST /feedback` and a review queue *(milestone 3)* | Reviewer decisions feed back as candidate eval cases and guardrail updates |

What does not carry over: the conversations are synthetic, the labels come from one
non-clinician, and the reference material is a short public summary rather than a clinic's
approved content. See the caveats above.

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
