# Clinic configs

A clinic config holds everything the judge reads that belongs to one clinic: its rubric,
reference sheet, protocol, staff role names and patient record fields. The code has no clinic
text in it. Two clinics exist: Fernhill (the research clinic) and Harbourline (a text-based
mental-health support service). Both are fictional, and all their data is synthetic.

## Folder layout

```
clinics/<id>/
  clinic.yaml    the config (the folder name must equal id)
  rubric.md      same structure as docs/rubric.md
  reference.md   facts the judge may rely on
  protocol.md    what the clinic's staff are meant to do
```

Fernhill's folder holds only `clinic.yaml`. It points at the files the research harness has
always used (`docs/rubric.md`, `data/reference/`), so the generation, labelling and leakage tools
are unchanged.

## clinic.yaml keys

- `id`: short name, same as the folder.
- `name`: display name.
- `description`: optional, one line.
- `staff`: responder role -> how the judge prompt names that person, for example `peer: peer support worker`.
- `files`: `rubric`, `reference` and `protocol`, each a path relative to the clinic folder.
- `protocol_start`: optional heading. The protocol is read from that heading onward (the text above it is for humans). It must exist in the file.
- `record_fields`: the patient record schema, one entry per field, `{type: str|int|list, required: true|false}`. A list must hold text only. A yes/no belongs in a `str` field.
- `record_schema: fernhill`: used instead of `record_fields` by Fernhill, so it keeps the typed record and responder checks. Set exactly one of the two.

Unknown keys, a missing file or a missing `protocol_start` stop the load with an error.

## rubric.md

Under `###` headings Safety, Escalation, Accuracy, Scope and Grounding, each section needs a
paragraph starting **Definition.** and one starting **Boundary.** After them comes a `## Severity`
table, then a "When unsure" paragraph. The judge copies these word for word, so a missing part fails
the check below.

## reference.md

One fact per line: `- **H01** statement text [S1]`. The id is letters then digits. The source tag
is optional, and anything after it on the line is ignored. A `## Sources` section is skipped, so
a source list is not read as facts. At least one fact is required.

## Checking a clinic

```
python -m safety_eval.clinic
python -m safety_eval.clinic harbourline --render tests/fixtures/clinics/harbourline_conversations.json
```

The first checks every clinic and prints its fact count and roles. The second also prints the
judge prompt (version C unless `--version` is given) for the first conversation in that file.
A conversation is checked against the clinic: the responder must be a `staff` role and the
record must match `record_fields`.

## Cache keys

Fernhill's keys are unchanged, so the committed cache still matches. Other clinics add the clinic
id and a hash of `clinic.yaml`, rubric, reference and protocol. Editing any of them re-judges.

## Still Fernhill-only

- Prompt D's worked examples (`prompts/examples.md`) are Fernhill conversations. Other clinics pass `examples=""` for now.
- Baseline rules (version A) and `run_judge.py` with the test-set lock are research tools and work for Fernhill only.
