# Decisions

Each entry records a non-obvious choice and why it was made. Newest at the bottom.
Entries marked **(Justin)** are Justin's decisions; the rest were made by Claude Code and
can be overturned.

## D1 — One medication: weekly semaglutide injection (step 1)

The patient record has a medication field, but every patient in the test set uses the same
medicine. Covering two medicines would split 25–35 reference statements between them, so each
medicine would be covered thinly, and the Accuracy criterion would get harder to label
consistently. One medicine keeps the reference sheet complete enough to label against and
easy to explain. The medication field stays in the record so a second medicine can be added
later without changing the schema.

## D2 — Generic drug names only (step 1)

The brief forbids real company names in the repo. Brand names of weight-loss injections are
company products, so the data, prompts and docs use only the generic name "semaglutide".
Sources were chosen from public health services (MedlinePlus, NHS) instead of manufacturer
leaflets for the same reason.

## D3 — MedlinePlus wins where sources disagree on missed doses (step 1)

MedlinePlus (S1) says a missed weight-management dose may be taken within 2 days; the NHS page
(S2) says within 5 days, but that page also covers the diabetes products. A reference sheet
that contains two rules for one situation cannot be used to judge Accuracy, so the clinic
follows one: S1, because it describes the weight-management use specifically. The conflict
is written down in `data/reference/sources.md`.

## D4 — A separate, fictional clinic protocol for Scope and Escalation (step 1)

The reference sheet says what is true about the medicine. It cannot say what a coach is
allowed to tell a patient, or where a red flag should be sent: those are clinic rules, not
medicine facts. Scope and Escalation can only be judged against written rules, so
`data/reference/clinic_protocol.md` defines responder roles and three escalation tiers for
the fictional clinic. The judge sees it alongside the reference sheet. It is a draft for
Justin to edit, because it decides what counts as a failure.

## D5 — Statements say "urgent care" without phone numbers (step 1)

The sources are from the US and the UK and use different emergency numbers. The conversations
are set at a fictional clinic with no country, so the reference sheet and protocol speak of
"emergency services" and "the clinic's urgent line" instead. This keeps the generators from
being judged on which number they chose, which is not what the eval is about.

## D6 — How `claude -p` will be called for judging (step 1, to verify in step 6)

From `claude --help` (Claude Code 2.1.293), the flags planned for each judge call are:

- `-p` / `--print`: non-interactive, prints the response and exits
- `--tools ""`: disables every built-in tool, so the judge cannot read files or run commands
- `--strict-mcp-config` with no `--mcp-config`: loads no MCP servers
- `--disable-slash-commands`: no skills
- `--system-prompt`: replaces the default Claude Code system prompt, which otherwise contains
  the working directory and git status
- `--output-format json`: one JSON result, which should include the model actually used
- `--model`: pins the model so the cache key is meaningful
- `--no-session-persistence`: judge calls are not saved as resumable sessions

Each call runs with its working directory set to a fresh empty temporary directory. `--bare`
was rejected because it only accepts an API key, and the brief requires the subscription.
The exact JSON shape of `--output-format json` will be checked with one real call in step 6
before any batch runs.

## D7 — Toolchain versions (step 1)

Python 3.13 is installed in the build environment (the brief asks for 3.11+). promptfoo is
pinned to 0.124.0 and run with `npx promptfoo@0.124.0`, so it needs Node but no global install.

## D8 — Build order for milestone 1 (step 1)

1. Scaffold and reference material
2. Data schemas, personas and scenario grid (needs `docs/patient_themes.md` from Justin)
3. Model assignment, generation pack and import script
4. Rubric draft, blind labelling tool, reconcile and self-consistency reports
5. Stratified splits and the leakage test
6. Model call layer, response cache, judge, and checks around the judge
7. Prompt versions A–E and the promptfoo dev comparison
8. Test-set evaluation, reports and the single rebuild command
9. Drafts of the README, results summary and limitations
