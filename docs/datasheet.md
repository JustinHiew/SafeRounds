# Datasheet

This project evaluates one judge with **three separate evaluation sets**. They answer different
questions, use different definitions of "correct", and are **never pooled into one score**. Counts
quoted here come from files in `reports/` (named in brackets).

## Summary

| | Core set | Natural-error set | External check |
|---|---|---|---|
| Question | How well does the judge catch each failure type at this clinic? | Does that hold for mistakes nobody designed? | Does the judge's idea of escalation match clinicians'? |
| Size | 250 conversations | 30 conversations (planned) | 100 graded replies |
| Source | Synthetic, written by three models from scenario cards with planted mistakes | Synthetic, a simulated patient and a Claude Haiku responder given the real clinic rules, no planted mistakes | HealthBench meta-evaluation: model replies to health conversations |
| Ground truth | Justin's labels (one non-clinician), checked against the cards | Justin's labels | Two physicians who agreed |
| Criteria covered | All five | All five | Escalation only (approximately) |
| Used for | Train (prompt examples), dev (choosing a prompt), test (headline) | Final evaluation only | Final evaluation only |

## 1. Core set

**Motivation.** Real patient conversations cannot be published, and public medical dialogue
datasets have no labels for these criteria. A synthetic set with planted mistakes gives known
intended answers and enough failures of every type to measure recall.

**Composition** (`reports/scenario_grid_summary.json`). 150 conversations with one planted failure
(30 per criterion, 15 obvious and 15 subtle), 40 hard negatives (a worrying situation handled
correctly) and 60 normal conversations. 68 are high severity by design. Responder type, writing
style, way of presenting the problem and anxiety level are balanced within each failure type.

**Collection.**
- Scenario cards come from 140 hand-written blueprints built on published patient-experience
  research and a public reference sheet (`docs/patient_themes.md`, `data/reference/`).
- ChatGPT, Gemini and Claude each wrote about a third of the conversations, with an equal share of
  every failure type (`generation/assignment.json`), from identical prompts (version gen-v3).
- Every conversation passed automated checks for format, length and leaks before import
  (`reports/import_report.json`). Realism measures are compared per model
  (`reports/generation_quality.json`).

**Labelling.** One non-clinician (Justin) labels every conversation blind to the card and source
model, then reconciles disagreements with the cards. He saw the generation prompts while pasting
them, so labels are described as exposed to the prompts rather than fully blind. A 40-conversation
relabel measures his self-consistency. Ambiguous conversations are excluded from headline metrics
and reported separately.

**Splits** (`reports/split_summary.json`). Train 34, dev 72, test 144, grouped by situation family
so near-duplicates never straddle splits, and balanced by failure type, severity and source model.

**Known biases.**
- One author (Claude Code) wrote all the scenarios and the clinic policy; the judge is also likely
  to be a Claude model.
- About 60% of conversations contain a failure, far above any real base rate.
- Escalation failures are almost all high severity, so high-severity recall leans on Escalation
  and Safety.
- The generators differ in style: ChatGPT and Gemini write shorter replies than Claude, so their
  subtle mistakes are easier to spot.

## 2. Natural-error set

**Motivation.** Planted mistakes may be easier to catch than real ones, and may share blind spots
with the judge. This set contains only what a responder actually said.

**Composition.** 15 new situations (not reused from the core set), two personas each, generated
four turns at a time. Status: built, not yet run.

**Labelling.** As the core set, but with no card to reconcile against: whatever is wrong is
whatever Justin finds.

**Known limits.** Small; all-Claude; may contain few failures, in which case it measures false
alarms rather than recall.

## 3. External check (HealthBench)

**Motivation.** The only set with clinician labels. It tests whether the judge's escalation
judgement agrees with physicians outside this project's own scenarios.

**Composition** (`data/external_ids/healthbench_sample.json`). 100 replies: 25 each of emergency
and context-dependent cases, each where both physicians marked the reply as meeting or not meeting
the emergency-behaviour criterion. Non-emergent cases are left out because physicians count
over-escalation as a failure there and the clinic protocol does not.

**Ceiling** (`reports/healthbench_physician_agreement.json`). Physician-to-physician agreement on
the same file frames how well any judge can be expected to agree.

**Licence and handling.** Code and data MIT-licensed; the raw file is downloaded by script and kept
out of the repository because it carries a canary string against redistribution into training data.

**Known limits.** Conversations are mostly synthetic or physician red-teaming, general health
rather than this medicine, US-centred, and the physicians' criterion bundles harm and clarity with
urgency.

## Background material (not evaluation data)

- `data/reference/`: the reference sheet and clinic protocol the judge is given.
- MeQSum consumer questions and three published studies: used only to shape the synthetic
  patients. No text from them appears in any conversation.

## How to read results across sets

See `docs/analysis_plan.md` section 4. In short: agreement across all three sets is evidence the
judge works; a gap between the core set and either check is reported as a finding about the core
set's realism, not hidden.
