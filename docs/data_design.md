# Data design

How the test material is built, from hand-written blueprints to scenario cards. Counts quoted
here come from `reports/scenario_grid_summary.json` (key names in brackets).

## Files and who may see them

| File | Contents | Judge sees it? |
|------|----------|----------------|
| `data/scenario_blueprints.yaml` | 140 hand-written blueprints: a situation, the planted mistake (obvious and subtle versions) or what a correct reply must do | No |
| `data/scenarios.yaml` | 250 scenario cards generated from the blueprints: the intended answer for each conversation | No |
| `data/conversations/` *(step 3)* | Conversation text and patient record only | Yes |
| `data/provenance/` *(step 3)* | Which model wrote each conversation | No |
| `data/labels/` *(step 4)* | Human labels and split ID lists | No |

The shapes are defined in `src/safety_eval/schemas.py`. Every model rejects unknown fields, so a
conversation file cannot carry a label or a scenario field without failing validation.

## From blueprint to card

A **blueprint** is one realistic situation, written by hand, tied to a patient theme
(`docs/patient_themes.md`), to the reference statements it depends on, and to the escalation tier
the clinic protocol requires. `python -m safety_eval.grid` turns each blueprint into two cards:

- **Failure blueprints** (75; 15 per criterion) give one card with the obvious version of the
  mistake and one with the subtle version. That makes exactly 30 cards per criterion, split 15/15
  (`failures_by_criterion.*.difficulty`).
- **Hard-negative blueprints** (20) give two cards each: a worrying situation handled correctly
  (`case_type.hard_negative`).
- **Normal blueprints** (30) give two cards each: everyday questions handled correctly
  (`case_type.normal`).

- **Natural blueprints** (15) give two cards each (`natural_cards`). No mistake is planted: a
  model plays the responder with the real protocol and its replies are labelled as they are. These
  30 cards are a separate held-out set (decision D23); every other count on this page is for the
  250 core cards.

For each card the generator then draws:

- **a responder**: the AI assistant, a health coach or a support agent, picked from those the
  blueprint allows, choosing whichever has been used least within that failure type
  (`failures_by_criterion.*.responder`);
- **a persona**: writing style, way of presenting the problem and anxiety level, balanced the same
  way (`writing_style`, `presentation`, `anxiety`), plus a first name;
- **a patient record** that satisfies the blueprint's constraints (for example "on gliclazide" or
  "first three weeks of treatment"), with everything else sampled. Background conditions are
  limited to common, stable ones so a random record never adds a red flag the blueprint did not
  intend.

All randomness comes from one fixed seed, so the grid is identical on every rebuild; a test checks
that the committed `data/scenarios.yaml` matches a fresh build.

## What a card records

`case_type`, `theme_id`, `responder`, `persona`, `patient_record`, `situation`,
`escalation_tier`, `expected_severity`, `facts`, `sensitive`, and either `planted_failure`
(criterion, obvious or subtle, a description of the mistake, and other criteria it is expected to
break) or `correct_reply` (what a correct reply must do). `blueprint_id` links the two cards made
from the same blueprint, which matters for the splits (decision D19).

## Known imbalances

- **Support agents** reply in fewer conversations than the other two responders (`responder`),
  because many topics (exercise, nutrition, mood) would not reach them. Within each failure type
  they appear in 4–6 of the 30 cards.
- **Escalation failures** are mostly high severity (`failures_by_criterion.escalation.severity`),
  because a missed red flag usually is. Accuracy and Grounding failures are mostly low severity.
  The headline metric (recall on high-severity cases) therefore leans on Escalation and Safety.
