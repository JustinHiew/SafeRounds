# Evaluation concepts

Plain-English notes on each idea this project uses, each tied to an example from the project.
Updated as the build goes on. Examples marked *(planned)* describe how the concept will be used
in a later step; they become concrete once that step is built.

## LLM-as-judge

Using a language model to grade another piece of text against written criteria, instead of
asking a person every time. It is cheap and fast, but it is itself a model that makes mistakes,
so it has to be tested like any other classifier before anyone trusts it.

*Here:* the judge reads one patient-support conversation, the patient record and the clinic's
reference material, and returns pass, fail or unsure for each of the five criteria. The whole
project exists to measure how often it is right.

## Error analysis

Reading real outputs before deciding what to measure. You write down what actually goes wrong,
then build the rubric around those failures, rather than inventing categories in advance.

*Here (planned):* Justin reads 30 random conversations with no rubric and writes free notes;
those notes shape the first frozen rubric.

## Held-out set (train / dev / test)

Data split into parts that play different roles. **Train** supplies examples you may show the
model. **Dev** is where you try ideas and compare versions; you look at it as often as you like.
**Test** is locked away and used once, at the end. If you tune a prompt while looking at the
test set, its score stops being an honest estimate of how the judge does on new data, because
you have quietly fitted the prompt to those exact cases.

*Here (planned):* 20% train, 40% dev, 40% test, stratified by criterion and source model. A test
fails the build if any test conversation ID appears in a prompt file.

## Stratified and grouped splits

A **stratified** split keeps the mix of each important category the same in every part, so the
test set is not, by bad luck, all Escalation cases written by one model. A **grouped** split keeps
items that are near-copies of each other in the same part, so the model is never tested on a
near-copy of something it was shown.

*Here (planned):* each blueprint produces two similar cards (an obvious and a subtle version of the
same mistake). Both go to the same split, and the split is stratified by criterion and source
model (decision D19).

## Leakage

Any way the answer can reach the model being tested, other than by actually doing the task. It
makes results look better than they will be in real use.

*Here:* the judge never sees scenario cards, labels or which model wrote a conversation; IDs are
random so they reveal nothing (decision D17); conversation files are validated so they cannot
carry label fields. From step 6, judge calls will also run with no tools from an empty folder,
so the judge cannot read the repo.

## Precision and recall

For a yes/no flag:

- **Precision** = of the conversations the judge flagged, the share that really had a problem.
  Low precision means many false alarms, which wastes reviewers' time.
- **Recall** = of the conversations that really had a problem, the share the judge flagged.
  Low recall means missed problems.

They trade off: flagging everything gives perfect recall and poor precision. A **confusion
matrix** is the 2×2 table behind both numbers (true positives, false positives, false
negatives, true negatives).

*Here (planned):* the headline number is recall on high-severity cases, because a missed
dangerous reply is worse than a false alarm that a clinician dismisses in a minute.

## Cohen's kappa

A measure of agreement between two raters that subtracts the agreement you would expect by
luck. If 80% of conversations pass, two raters who both say "pass" at random agree often
without any skill; plain percentage agreement hides that. Kappa is 1 for perfect agreement, 0
for chance-level, and can be negative. A rough reading: below 0.4 weak, 0.4–0.6 moderate,
0.6–0.8 substantial, above 0.8 strong, but these bands are a convention, not a law.

*Here (planned):* kappa between the judge and Justin's labels, and between Justin's first and
second labelling passes. The second number shows how reliable the human standard itself is,
which caps how good the judge can look.

## Few-shot prompting

Putting a handful of worked examples inside the prompt so the model can copy the pattern.
Examples must come only from the train split; if a dev or test conversation were used as an
example, the judge would be graded on a question it had already been shown the answer to.

*Here (planned):* prompt version D is version C plus train-split examples, each with the
reasoning written out.

## Ablation

Removing or changing one part of a system at a time to see how much that part contributes.

*Here (planned):* prompt versions A to E form an ablation ladder: rules with no model (A), a
one-line instruction (B), a full prompt (C), plus examples (D), split into one call per
criterion (E). Comparing neighbours shows what each addition buys.

## Bootstrap confidence interval

A way to show how much a metric could move just because the test set is small. You resample
the test set with replacement many times (say 2,000), recompute the metric on each resample,
and take the middle 95% of the results as the interval. A recall of 0.90 with an interval of
0.75–0.98 tells a reader much more than 0.90 alone.

*Here (planned):* every headline metric in `reports/` carries a bootstrap 95% interval.

## Regression test

A check that reruns a fixed set of cases whenever something changes, and fails if quality drops
below an agreed bar. It stops a well-meant prompt edit or model upgrade from silently making
things worse.

*Here (planned):* a promptfoo config reruns the dev set when the prompt or model changes and
fails if high-severity recall drops below a threshold that Justin sets after seeing the results.

## Hard negative

A case that looks like it should be flagged but should not be. Without them, a judge can score
well by flagging anything that sounds alarming.

*Here (planned):* at least 40 conversations where a patient describes something worrying and
the reply handles it correctly, for example by escalating straight away.
