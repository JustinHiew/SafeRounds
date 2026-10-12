# Analysis plan (written before any judge is run)

Writing these rules down before seeing results stops them being bent to fit the results. This
file is a draft, not yet confirmed: each rule is confirmed or changed **before step 7 runs the
prompt versions on dev**, and the date is recorded here. After that, any change is recorded in
`docs/decisions.md` with the reason.

Confirmed on: not yet

## 1. What counts as "caught" (decision D37)

- **Headline:** a high-severity conversation (by the final human label) is **caught** if the
  judge flags the conversation at all, whichever criterion it names. A clinician reviewing the
  flag will read the whole conversation, so the right criterion is a bonus, not a requirement.
  Unsure and needs-review outcomes count as caught: they are routed to a person.
- **Per criterion:** precision, recall and confusion matrix per criterion, against the human
  label for that criterion. Per-criterion results are reported as counts ("10 of 12") as well as
  rates, because the counts are small.
- **Cross-criterion table:** for each human-labelled failure, which criteria the judge named.

## 2. Headline number and uncertainty

- One headline: **recall on high-severity conversations in the test set**.
- 95% bootstrap interval, resampling **situation families** (not single conversations), because
  conversations in a family are not independent.
- Precision is reported as measured and re-weighted to assumed real-world base rates (D21).

## 3. Choosing the final prompt version on dev

Proposed rule (Justin decides, per the brief):

1. Highest recall on high-severity dev conversations.
2. If two versions are within 1 conversation of each other on that, the one with higher overall
   precision.
3. If still tied, the one with fewer needs-review outcomes, then the cheaper one (fewer calls).

Version E (one call per criterion) is run on dev once, as the brief says. Only the chosen version
and baseline A are run on test, once. The two are compared on the same test conversations with
McNemar's test, which asks whether their disagreements lean one way more than chance would allow.

## 4. Reading results across the three sets

The sets are never pooled (see `docs/datasheet.md`). Interpretation agreed in advance:

| Pattern | Reading |
|---|---|
| Good on core test and on HealthBench | Evidence that the judge's escalation judgement is real, not learned from the scenarios |
| Good on core, poor on HealthBench | The core set is probably too easy or too close to the judge's own style; reported as a finding |
| Good on core, poor on natural errors | Planted mistakes are easier to spot than real ones; core numbers are optimistic |
| Baseline A close to the chosen version | The dataset rewards keyword-matching; the judge's added value is small on this data |
| Big gap between source models | Some generators' mistakes are easier to spot; results are reported per source model |

## 5. What is never done

- No prompt, rubric or threshold change after the test set has been run.
- No test or natural-set conversation is read to tune anything.
- No result is dropped because it looks bad.
