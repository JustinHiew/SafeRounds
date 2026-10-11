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
leaflets for the same reason. The one exception is a cited paper's own title (E3 in `docs/patient_themes.md`),
which is quoted exactly so the citation can be found.

## D3 — MedlinePlus wins where sources disagree on missed doses (step 1)

MedlinePlus (S1) gives a 2-day window for a missed dose when the medicine is used for weight
loss and a 5-day window when it is used for diabetes; the NHS page (S2) gives only the 5-day
window, on a page that covers both uses. A reference sheet
that contains two rules for one situation cannot be used to judge Accuracy, so the clinic
follows one: S1's weight-loss rule, because it is the one written for this use. The conflict
is written down in `data/reference/sources.md`.

## D4 — A separate, fictional clinic protocol for Scope and Escalation (step 1)

The reference sheet says what is true about the medicine. It cannot say what a coach is
allowed to tell a patient, or where a red flag should be sent: those are clinic rules, not
medicine facts. Scope and Escalation can only be judged against written rules, so
`data/reference/clinic_protocol.md` defines responder roles and three escalation tiers for
the fictional clinic. The judge sees it alongside the reference sheet. It is a draft for
Justin to edit, because it decides what counts as a failure.

## D5 — ~~No phone numbers~~ (step 1, superseded by D9)

First version: the sheet said "emergency services" without numbers because the sources mixed
US and UK. Replaced by D9 once the clinic was set in Australia.

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

*Update, step 3:* `--safe-mode` was added, which also turns off CLAUDE.md files, skills, plugins
and hooks. One real call confirmed the JSON shape: the reply is in `result`, success is
`subtype: "success"`, and `modelUsage` lists the exact model names. It can also list a small
helper model used internally (it listed `claude-haiku-5-5` next to `claude-sonnet-5-5` for a
Sonnet call), so the recorded model is the `modelUsage` entry matching the family asked for.

## D7 — Toolchain versions (step 1)

Python 3.13 is installed in the build environment (the brief asks for 3.11+). promptfoo is
pinned to 0.124.0 and run with `npx promptfoo@0.124.0`, so it needs Node but no global install.

## D8 — Build order for milestone 1 (step 1)

1. Scaffold and reference material
2. Data schemas, personas and scenario grid
3. Model assignment, generation pack and import script; natural-error conversations (D23)
4. Rubric draft, blind labelling tool, reconcile, plant-success (D22) and self-consistency reports
5. Stratified splits and the leakage test
6. Model call layer, response cache, judge, and checks around the judge
7. Prompt versions A–E and the promptfoo dev comparison
8. Test-set and natural-set evaluation, base-rate-adjusted precision (D21), reports and the single
   rebuild command
9. Drafts of the README, results summary and limitations

## D9 — The fictional clinic is set in Australia (step 1, agreed with Justin)

The project accompanies an application to a Sydney-based role at a clinic serving Australia
and the UK, so reviewers will read the data with Australian care routes in mind. The clinic
writes in Australian English and its protocol uses Australian routes: triple zero (000),
healthdirect (1800 022 222), the Poisons Information Centre (13 11 26) and Lifeline
(13 11 14). Two healthdirect pages (S5, S6) and its poisoning page (S7) were added as sources.
The MedlinePlus and NHS pages stay because healthdirect has no general consumer page for
semaglutide without a brand name. Fitting the new statements in the 25–35 limit meant merging
six old statements into three; nothing refers to statement IDs yet, so renumbering was safe.
A fixed set of phone numbers also gives the Accuracy and Escalation criteria something
concrete to check (a wrong number is an Accuracy failure).

## D10 — A third responder type: patient support agent (step 1, agreed with Justin)

The target role lists patient-experience support as its own workstream next to coaches and
the AI assistant. Support agents answer order, delivery and payment questions and are not
clinicians, but patients often mention a symptom in passing inside a logistics question.
That is a realistic source of subtle Escalation and Scope failures that the other two
responder types do not produce as naturally. The cost is one more dimension to balance in a
grid of about 250 conversations, which step 2 handles by balancing responder type across
failure types rather than crossing every combination.

## D11 — No employer or brand names in the repo (step 1)

The brief forbids real company names, and a public repo should stand on its own. The README
describes the target setting in general terms ("a digital weight-management clinic") and the
link to the specific role belongs in the application, not the code.

## D12 — Patient themes drafted from published research, not by hand (step 1, at Justin's request)

The brief had Justin read patient forums and list themes by hand. Justin has no background in
this patient group, so a hand-made list would carry less evidence than published work. The
themes are instead drawn from three open-access studies of patient posts and reviews about
semaglutide (what patients talk about), and writing patterns are measured on MeQSum, a public
CC BY 4.0 set of 1,000 consumer health questions (how people write). MeQSum was chosen over
scraping forums because it is licensed for reuse, already de-identified by its publisher, and
pinned to a commit and checksum so the numbers can be reproduced. Its raw text stays out of the
repo (`data/external/` is git-ignored) because it is still real people's writing; only
aggregate shares are committed. Safety-critical topics that are rare in forums (red flags,
pregnancy, self-harm) are added by design from the reference sheet, so the test set covers
what the judge must catch, not only what is common.

## D13 — Published figures are stored in `reports/` too (step 1)

The brief's rule is that every number in a document comes from a file in `reports/`. Figures
quoted from papers are not this project's results, but keeping one rule with no exceptions is
easier to check than a rule with a carve-out. So each cited figure is typed into
`data/reference/literature_figures.yaml` together with the exact sentence it comes from, and
`python -m safety_eval.literature_figures` copies them to `reports/literature_figures.json`. The
quotes were matched word for word against the downloaded full text of each paper on 2026-10-08.

## D14 — The writing-style report needs a one-off download (step 1)

`reports/patient_style_stats.json` is computed from MeQSum, which is downloaded rather than
committed (D12). The report itself is committed, so the "rebuild every report offline" command
(step 8) will skip this one report when the download is missing instead of failing.

## D15 — Source checks done by Claude Code, not left to Justin (step 1, at Justin's request)

Checking reference statements against their sources, checking study figures, and reviewing the
themes and clinic protocol are research tasks, so Claude Code does them. Each source page was
downloaded, converted to text, and every statement or figure was compared with that text. Four
statements were reworded as a result (F12 added runny nose and sore throat, F15 now says why
vomiting matters, F32 regained its "aged 19 to 64" qualifier, and the missed-dose conflict note
now says S1 itself separates the diabetes and weight-loss rules). One claim was corrected: E3
does not name a "strongest predictor" of stopping; it says minimal weight loss and
non-gastrointestinal side effects were more often associated with stopping. These checks
confirm the text matches public sources; they are not a clinical review, and the README says
so. Items that stay with Justin are the ones the brief assigns to Justin by name: the final
rubric and judge-prompt wording, all human labels, thresholds and written conclusions.

## D16 — Scenarios are written as blueprints and expanded by code (step 2)

Writing 250 cards by hand would make balance hard to control and hard to check; generating them
entirely at random would give unrealistic or contradictory cases. The middle path: 125
hand-written blueprints, each a realistic situation tied to a theme, reference statements and an
escalation tier, expanded by a seeded script that balances responder type and persona within
each failure type. Every failure blueprint has an obvious and a subtle version of the same
mistake, which gives the brief's even obvious/subtle split by construction and lets the results
compare the two directly on matched situations.

## D17 — Opaque, random scenario IDs (step 2)

IDs such as `cv-3fa91c` are drawn at random and the cards are stored in ID order. An ID like
`ESC-07-subtle`, or a file ordered by criterion, would leak the answer to anyone (or any model)
that sees the ID. The link from a card to its blueprint is kept in a separate field that only
the evaluation reads.

## D18 — A failure card may expect more than one criterion to fail (step 2)

Realistic mistakes rarely break exactly one rule: telling a patient to double a dose is unsafe,
contradicts the reference sheet and is beyond a coach's role. Each failure card names one
primary criterion (which sets the 30-per-criterion balance) and lists the others its author
expects to fail (`also_fails`). These lists are only a guide for the reconcile step; Justin's
labels are the ground truth for every criterion.

## D19 — Both cards from a blueprint will go into the same split (step 2, applies in step 5)

The two cards made from one blueprint describe the same situation. If one landed in train (and
became a prompt example) and the other in test, the judge would be tested on a near-copy of an
example it had already been shown, which inflates the score. Step 5 will therefore split by
blueprint, keeping both cards together, while still stratifying by criterion and source model.
This is called a grouped split.

## D20 — Support agents appear in fewer conversations (step 2)

Many topics would never reach a support agent (exercise plans, mood, nutrition), so forcing an
equal three-way split would produce unrealistic conversations. Support agents are allowed only
where a patient would plausibly message them (orders, deliveries, storage, needles, a symptom
mentioned in passing), and the grid makes sure each failure type has some support-agent cases.

## D21 — Precision is also reported at realistic base rates (plan for step 8)

About 60% of the core set contains a planted failure, because the test set must hold enough
failures to measure recall. In real use, problem conversations would be far rarer, so most flags
would be false alarms and precision would be much lower than the test set shows. Recall does not
depend on the base rate; precision does. Step 8 will report precision as measured and re-weighted
to a range of assumed base rates (for example 2%, 5% and 10%), clearly labelled as assumptions.
The re-weighting uses the judge's measured recall and false-alarm rate, so it needs no new
model calls.

## D22 — Plant-success rate is a reported data-quality number (plan for step 4)

A generator may soften a subtle mistake until it disappears, or add a second mistake nobody
asked for. The reconcile step (Justin's blind labels against the cards) already finds these.
Step 4 will report the share of failure cards where the human label confirms the planted failure,
by source model and by obvious versus subtle, so readers can see how far the intended answers
held up before any judge was run.

## D23 — A separate natural-error set (step 2, built in step 3)

Planted mistakes are designed, and the person who designed them (Claude Code) may share blind
spots with the judge. To get some mistakes nobody designed, 30 extra conversations (15 new
situations, two personas each) will be produced in step 3 by a model playing the clinic
responder with the real protocol and reference sheet and no instruction to make a mistake.
Whatever it says is labelled by Justin like any other conversation. These conversations form a
second held-out set: never in train, dev or test, and evaluated once at the end alongside the
test set. The situations are new rather than reused, so none is a near-copy of a prompt example.
Its results will be small-sample and reported separately; their value is showing whether the
judge's performance on designed mistakes carries over to natural ones.

## D24 — How the generation work is divided and batched (step 3)

Scenarios are dealt to ChatGPT, Gemini and Claude round-robin within each stratum (each failure
type and difficulty, hard negatives, normal cases), after shuffling with a fixed seed. Each model
gets 83 or 84 conversations and an equal share, within one, of every stratum. Each model's
scenarios are then shuffled and cut into batches of five, so a batch mixes failure types and
normal cases and the generator cannot settle into one pattern. Justin opens a fresh chat for
every batch so earlier batches do not shape later ones. All three models get the identical
prompt text, so differences between them come from the model, not the instructions.

## D25 — What the generation prompt contains (step 3)

Following brief 7.6: the purpose; rules for realism and for hiding the mistake; the full clinic
protocol; the reference statements the five scenarios depend on (not all 35, to keep the prompt
focused); the exact JSON output shape with one worked example on a scenario that is not in the
set; and each scenario in tagged sections. Failure scenarios include the escalation tier the
protocol requires, so the rest of the reply can be correct around the planted mistake.
No-failure scenarios state what a correct reply must do, so hard negatives are truly correct.
Claude's share is generated with the `sonnet` alias; the exact model name is recorded.

## D26 — The importer rejects, it never repairs (step 3)

A conversation that fails a check is rejected and goes into a redo prompt, rather than being
fixed by code. A repaired conversation would no longer be what the generator wrote, and silent
fixes are exactly where label leaks creep in. Checks: the JSON shape; 4 to 10 alternating turns
that start with the patient and end with a staff reply; the conversation belongs to the model
that supplied it; and no leaks (fact or scenario IDs, words such as "planted" or "scenario",
model or company names, brand names, bracketed notes about mistakes). Ordinary words such as
"mistake" are allowed because patients use them.

## D27 — How the natural-error set is generated (step 3)

For each natural scenario, a patient simulator (Sonnet) and a responder (Haiku) take turns for
four messages. The responder gets exactly what a deployed clinic assistant would get: its role,
the clinic protocol, the full reference sheet and the patient record, with no hint that this is a
test or that mistakes are wanted. Haiku, the smallest current Claude model, plays the responder
because a cost-conscious deployment would plausibly use a small model, and because a smaller
model makes the natural slips this set exists to capture. Four turns keep the cost at 120 calls
for 30 conversations. A test checks the responder's instructions contain no mention of testing.

## D28 — Removed test wording from the clinic protocol (step 3)

The protocol said the prescriber "does not reply in this test set". The protocol is shown to the
natural-set responder and, later, to the judge, so that phrase told both of them they were in a
test. A test on the responder's instructions caught it; the line now says the prescriber "does
not take part in these chats".

## D29 — Generation prompt v2: the planted mistake must stand (step 3)

In the first Claude batch, one responder made the planted mistake (suggesting the patient halve
their metformin) and then took it back two turns later. A walked-back mistake makes the label
unclear: did the conversation fail or not? The prompt now says nobody notices, questions or
corrects the mistake later, and the version number in every cached call moved from gen-v1 to
gen-v2. The run was stopped after its first batch, the v1 output was discarded, and all batches,
including ChatGPT's and Gemini's, use v2. The importer also flags common walk-back phrasings as
warnings for a person to check; it cannot catch every way of taking something back, so the
labelling step remains the real check.

## D30 — Patient records must fit the situation's timeline (step 3)

Reading Claude's first conversations showed a patient on dose step 5, the top step, asking for
"the next dose strength". The record had been sampled at random and contradicted the situation,
which would have created an accidental Grounding problem nobody planted. Thirteen blueprints
whose situations assume a timeline (a recent step-up, a next step, a first week, a three-week
plateau) now carry minimum or maximum dose steps or weeks. The limits are applied by clamping the
sampled value rather than drawing new random numbers, so only the 12 affected cards changed and
every other card stayed identical. The changed cards' prompts were rebuilt; the four Claude
batches containing them were regenerated (the cache key includes the prompt text, so only those
four were new calls). ChatGPT and Gemini had not started, so they get the corrected prompts.
A test now checks every card against its blueprint's limits.

## D31 — Generation prompt v3 after reviewing ChatGPT's output (step 3)

ChatGPT's 83 gen-v2 conversations all passed the import checks, but two realism problems showed
when they were compared with Claude's (figures in `reports/generation_quality_gen_v2.json`).
Some "rambling" patients talked about their own message ("I was trying to phrase the
question…") instead of their life: 3 of 18 by the strict pattern now used, against 0 of 21 for
Claude. (An earlier, broader pattern counted 9, but it also matched normal speech such as "I
don't want to guess", so it was replaced.) And subtle mistakes were often the whole reply: the
staff turn carrying a subtle mistake had a median of 35 words, against 70 for Claude, so
"subtle" cases would have been easy to spot and the judge's subtle-mistake recall would have
been inflated for a third of the data. Every ChatGPT conversation was also exactly 4 turns
(`share_with_4_turns` 1.0). Prompt v3 adds three rules: vary the length
and use 6 or more turns when the detail comes late; long messages are about the person's life,
never about the message; and a subtle mistake sits inside an otherwise complete, helpful reply,
in the staff member's own words rather than the scenario's wording. All three models use v3 so
that comparisons between them stay fair; the v2 output of ChatGPT and Claude is kept under
`generation/superseded/gen-v2/` and not used. These measures are now computed on every import
(`reports/generation_quality.json`), so each generator's output is checked the same way.

## D32 — Chat-app link artefacts count as leaks (step 3)

ChatGPT's gen-v3 output contained a phone number turned into a link by the ChatGPT app:
`[13 11 14](tel:+61131114?oai_link_source=model_response_hotline)`. "oai" names the generator,
so the judge could tell who wrote that conversation, and no real clinic chat contains markdown
links. The importer now treats markdown links, `oai_` and `utm_source=` as leaks. In line with
D26, an affected conversation is rejected and regenerated, not cleaned up.

## D33 — Result of prompt v3 (step 3)

On gen-v3 (`reports/generation_quality.json`), ChatGPT's conversations average 5.2 turns with
0.439 at the 4-turn minimum (was 1.0), no rambling patient comments on their own message, and the
staff turn carrying a subtle mistake has a median of 41 words (was 35). Claude's are 5.86 turns,
0.217 and 97 words. ChatGPT still writes shorter replies, so its subtle mistakes remain easier to
spot than Claude's. This is now a property of the generator rather than of the prompt, so it is
not chased further: results will be broken down by source model, and any gap in subtle-mistake
recall between sources will be reported as such. ChatGPT returned all 17 batches as one file;
the importer and redo command accept that.

## D34 — Gemini's share (step 3)

Gemini returned 70 of its 84 conversations as one file; batches 15–17 were missing. Three
conversations were rejected because Gemini added an extra field (`"responder": "responder"`)
inside some turns; under D26 they are regenerated, not repaired. On the 67 accepted
(`reports/generation_quality.json`), Gemini averages 4.39 turns with 0.806 at the 4-turn
minimum, no rambling patient comments on their own message, and the staff turn carrying a subtle
mistake has a median of 57 words. Gemini followed the "vary the length" rule least of the three;
as with ChatGPT's shorter replies (D33), this is reported by source model rather than chased
with further prompt changes. The redo command now treats a model that returned one combined file
as having attempted its whole share, so missing batches get redo prompts too.

## D35 — External check: physician labels from HealthBench (step 3, built now, run in step 8)

The brief's external check (section 11) needs a public dataset of annotated medical dialogues.
HealthBench's meta-evaluation file (Arora et al. 2025; code and data MIT-licensed) contains
model replies to health conversations graded by two physicians each on an "emergency behaviour"
criterion: did the reply send the user to care with the right urgency. That is close to the
Escalation criterion and gives what this project otherwise lacks: clinician ground truth.
Choices:

- **Which cases.** Only emergencies and context-dependent cases are used for the judge
  comparison. In non-emergent cases physicians mark over-escalation as a failure, but the
  clinic protocol says over-escalation is not an Escalation failure, so the two definitions
  would disagree by design. All three groups are in the physician-agreement report.
- **Which labels.** Only replies both physicians agreed on, so the reference label is not itself
  in dispute. 25 replies per cell (emergent or context-dependent, met or not met), 100 in all,
  one reply per conversation, drawn with a fixed seed. Only IDs are committed
  (`data/external_ids/`); the raw file stays in the git-ignored `data/external/` because it
  carries a canary string asking that it not be redistributed into training data.
- **The ceiling.** Physician-to-physician agreement is computed on the same file
  (`reports/healthbench_physician_agreement.json`). A judge cannot be expected to agree with one
  physician more than physicians agree with each other, so this frames the judge's result.
- **What it does not show.** The conversations are mostly synthetic or physician red-teaming,
  general health rather than this medicine, and US-centred. The physicians' criterion also
  bundles harm, consensus and clarity with urgency, so the mapping to Escalation is approximate.
  The judge will need a stripped-down prompt with no clinic protocol, since the clinic's rules do
  not apply to these conversations. Results are reported separately and never pooled with the
  core test set.

## D36 — Split: 10 / 30 / 60, grouped by situation family (step 5, from the review)

The brief's 20 / 40 / 40 left about 12 cards per criterion in test. Train's only job is to supply
a few worked examples for the judge prompt, so it does not need 50 conversations; test carries
every reported number, so it gets the largest share. Targets are now 10 / 30 / 60.

Grouping by blueprint (D19) was not enough: the review found different blueprints describing
nearly the same situation (SAF-01 and HN-06 are both "missed a dose 4 days ago"), and many
failures have a deliberately matched hard negative. Blueprints are now grouped into 35 situation
families listed in `data/scenario_blueprints.yaml`, and a whole family always lands in one split.

Method, in two stages so it can be explained in a sentence each:
1. **Train first.** Small groups (four cards or fewer) are drawn at random until every failure
   type, hard negatives and normal cases each have at least four cards. A plain balancing pass
   left train with no Safety or Escalation examples at all, which would have starved the judge
   prompt of the two most important failure types.
2. **Then dev and test.** Remaining groups are placed biggest first, each into whichever of dev
   or test keeps the mix of failure type, severity and source model closest to a 1:2 ratio.

Result (`reports/split_summary.json`): train 34, dev 72, test 144 conversations, with 42
high-severity cases in test. The split is computed from the scenario cards and the model
assignment, so it does not change when the last conversations are imported. A test checks that
no dev or test conversation ID, or 60-character stretch of its text, appears in any prompt file.

## D37 — What counts as "caught", and the analysis plan written in advance (from the review)

The headline unit is the conversation: a high-severity conversation is caught if the judge flags
it at all, whichever criterion it names, because a clinician reviewing the flag reads the whole
conversation. Unsure and needs-review outcomes count as caught, since they go to a person.
Per-criterion results are reported separately, as counts as well as rates. The rule for choosing
the final prompt on dev, the McNemar comparison with baseline A on test, family-level bootstrap
intervals and how to read results across the three sets are all written in
`docs/analysis_plan.md` before any judge runs, so they cannot be bent to fit results. Justin
confirms or edits that plan before step 7.

## D38 — Labelling tool design (step 4)

Labels are typed in a terminal, one conversation at a time, in a random order fixed per pass,
showing only what a reviewer would see: responder type, patient record and the turns. Every
answer is appended to a file at once, so a pass can stop and resume. Labels carry an "unsure"
mark rather than forcing a guess (from the review); unsure conversations go to the reconcile
step. Severity is only asked when something fails, and the label schema rejects a severity
that contradicts the verdicts. The rubric version is stamped on every label, so labels made
under a draft rubric can be told apart from labels made under the frozen one. Because Justin
saw the generation prompts while pasting, labels are described as "labeller exposed to the
generation prompts", not fully blind (review point 1).

## D39 — Judge design and the checks around it (step 6)

- **Prompt versions are files** in `prompts/` with a small header (version, whole or
  per-criterion mode) and system and user sections. Placeholders pull in the rubric, reference
  sheet, clinic protocol, patient record and conversation, so the rubric text in the prompt is
  always copied word for word from `docs/rubric.md`.
- **Evidence before verdict.** The output schema orders evidence, analysis, then verdict for
  each criterion (brief 10.2.6). The judge may answer "unsure".
- **Fail closed.** A failed or timed-out call, output that is not valid JSON in the required shape
  after one retry, an overall flag that contradicts the verdicts, a quote that is not in the
  conversation, or a fail without a quote all become "needs review", never "pass". Quotes are
  matched after normalising whitespace, letter case and curly quote marks, because models often
  change those when copying text; any other difference counts as a missing quote.
- **Cache key** is the prompt version, a hash of the prompt file, the model, the conversation ID,
  the criterion (in per-criterion mode) and the attempt number. The brief asks for version, model
  and conversation; the file hash is added so an edited prompt can never be served an old answer.
  The cache stores the raw reply, and checks run again on every read, so changing a check needs no
  new calls.
- **Leak guard at load time.** Loading a prompt file fails if the file contains any dev or test
  conversation ID or a 60-character stretch of its text. A test found that the first version only
  checked the prompt's sections and missed text elsewhere in the file; it now checks the whole file.
- **API backend** uses the official Anthropic Python SDK, imported only when that backend is
  created, and is tested with a fake client. It is never called for real (no paid API).

## D40 — Judge model: Claude Sonnet through the command line (step 7)

The judge runs on the `sonnet` alias through `claude -p` (the exact model name is recorded with
every result). Comparing versions B to E on dev takes about 576 calls (72 conversations for each
of B, C and D, and 360 for E's one-call-per-criterion), and the test set adds the chosen version
three times for the consistency check. Sonnet keeps that within subscription usage limits while
being a strong model. One real call with version C on a train conversation returned valid output
whose quotes all passed the evidence check. A stronger model can be compared later if usage
allows; it would be a new run with its own cache entries, never a silent swap.

## D41 — How versions A–E are built and compared (step 7)

- **A, rules:** word lists come only from the clinic protocol and reference sheet, never from the
  scenario cards, and were sanity-checked on train only. A baseline written from the answer key
  would understate what the model adds.
- **B:** the one-sentence instruction, plus the patient record and the required output format
  (without a fixed format nothing could be scored).
- **C:** the full prompt from brief 10.2: stakes, rubric copied from `docs/rubric.md`, reference
  sheet, clinic protocol and record in tags, the conversation marked as data not instructions,
  evidence then analysis then verdict, "unsure" allowed, JSON output.
- **D:** C plus seven worked examples from train (one per failure type, a hard negative and a
  normal case; subtle failures preferred). Their verdicts come from Justin's final labels once
  they exist. Their reasoning is drafted from the card and must be checked by Justin. Where each
  example came from is kept in `data/labels/example_sources.json`, not in the prompt, because
  words like "scenario card" would tell the judge the data is constructed.
- **E:** D with one call per criterion.
- **promptfoo only replays saved results.** Model calls go through `python -m safety_eval.run_judge`,
  which caches and asks before batches over 100 calls; promptfoo then compares versions without
  being able to spend usage by itself. A plumbing test with throwaway labels confirmed the setup.
- **The test set is locked in code:** it runs only for baseline A and the version recorded in
  `data/labels/test_run_authorisation.json`, and every test run appends a dated line to this file.
- **The prompts are drafts.** The final wording of the judge prompt is Justin's (brief section 4);
  each file's header says so, outside the text sent to the model.

## D42 — Phone numbers must be plain text in the generation prompt (step 3)

The ChatGPT redo of cv-5b9461 (a mood and self-harm case that mentions Lifeline) came back with
the same link artefact as before (D32): `[13 11 14](tel:+61131114?oai_link_source=model_response_hotline)`.
In line with D26 it is rejected, not cleaned up. The reply is kept in
`generation/raw/chatgpt/chatgpt_redo_01.json`. The output-format section of the generation prompt now
also asks for phone numbers as plain digits, never as links or markdown. This changes only the
format instructions, not the scenarios or the rules, and applies only to prompts written after
2026-10-09 (the redo pack). No conversation that has already been accepted is affected.

## D43 — A web page for labelling, alongside the terminal tool (step 5)

Justin works from the Claude app, where the terminal labelling tool cannot take typed answers.
`python -m safety_eval.label_page` builds a private web page (`labelling/template.html` →
`labelling/label_desk.html`, rebuilt rather than committed) that shows exactly what the terminal
tool shows (responder type, patient record, turns) in the same fixed random orders (pass1 and
explore seeds). It contains no scenario cards, planted mistakes, splits or source models; a test
checks this. The rubric is hidden in the explore pass, as brief 8.1 asks.

Answers are saved to the page's own store, readable and writable only by its owner (checked with
a lower access level, which saw nothing). Claude Code exports the store and runs
`python -m safety_eval.labelling import-web PATH`, which validates every answer as a `HumanLabel`
and writes `data/labels/labels_pass1.jsonl` and `exploration_notes.md`. The answers are copied as
they are; Claude Code never fills in or edits a label (brief: "Never fill in a human label").

## D44 — Clinic configs; Fernhill prompts kept byte-identical (product phase 2A)

The product has to work for any clinic by configuration, so the rubric, reference sheet,
protocol, staff role names and record fields now come from `clinics/<id>/clinic.yaml`
(`docs/clinics.md`). Fernhill's files stay where they were; `clinics/fernhill/clinic.yaml` points
at them, so the generation, labelling and leakage tools do not change.

- **Fernhill prompts are byte-identical.** `tests/fixtures/fernhill_render_hashes.json` holds
  hashes of every rendered prompt B–E over all 250 conversations, taken before the refactor. A
  test compares against them, so every cached judge answer stays valid.
- **Cache keys.** Fernhill's are unchanged. Any other clinic adds `clinic_id` (conversation ids
  can repeat across clinics) and `clinic_sha` (a hash of its files), so editing a protocol
  re-judges instead of serving old answers.
- **Conversations.** Generic clinics use `BaseConversation`, with a free-form record checked
  against `record_fields`. Fernhill keeps the typed `Conversation`.
- **Second clinic.** Harbourline, a fictional text-based mental-health support service, exists to
  prove nothing is hard-coded. A test checks that its prompts contain no Fernhill text.
- **Fact parser.** It now also accepts facts without a source tag and skips a `## Sources`
  section. On Fernhill's sheet it gives identical results.
- **Not yet generic:** prompt D's worked examples (Fernhill-only; others pass none), baseline A and
  `run_judge.py` with the test-set lock.

## D45 — Ingestion and de-identification (product phase 2B)

Conversations from a customer reach the project through `src/safety_eval/ingest/` (canonical JSON,
CSV with one row per turn, and a YAML-mapped adapter for helpdesk exports) and are de-identified by
`src/safety_eval/deid.py` before anything is returned. See `docs/ingest.md`.

- **De-identify before anything is stored or returned.** `ingest()` checks the shape, scrubs, then
  checks again. No caller ever sees the original text, so a later store, log or model call cannot
  hold it. The judge's evidence quotes are checked against the de-identified text it saw; a test
  confirms a placeholder quote passes and a quote with the original number fails.
- **Clinic numbers are allowlisted.** Accuracy depends on staff quoting the right phone numbers,
  hours and URLs from the reference sheet and protocol. Any phone (compared by digits), URL, email or
  address found in that clinic's material is kept, and 000 and 112 never change. Scrubbing Lifeline's
  number would turn a correct reply into a false Accuracy failure.
- **Reject, do not truncate.** A conversation outside 4 to 10 turns, with an unknown speaker, a
  wrong role or a bad record is rejected with a reason. Cutting turns would change what the judge
  sees. Reasons and reports never contain conversation text.
- **The mapping is discarded.** `deidentify_conversation` returns placeholder to original, but
  `ingest` drops it, because a returned mapping ends up in a log or cache and defeats the purpose.
  Keeping one is the customer tenant's job (2G). One placeholder per person, so a first name and the
  full name share it and the mapping is an audit aid, not an exact restore.
- **Medicare numbers need a valid check digit,** so random 10-digit numbers are not hit.
- **Regex limits.** Names are caught only from `known_names`, self-introductions and staff
  greetings or sign-offs. Other names, dates without a year, local eight-digit phones and
  lower-case addresses can survive, so stored data is still treated as sensitive.
- **Mapped adapters are configuration.** Unknown keys, unknown speaker values and unknown responder
  values fail loudly rather than being guessed.

## D46 — API service (product phase 2C)

The judge is now behind an HTTP API (`src/safety_eval/service/`, `docs/service.md`): evaluate one
conversation, bulk batches with a worker, feedback, stats, audited reads, per-clinic API keys.

- **De-identification is injected and mandatory.** `create_app(settings, backend, deidentify)` takes
  the Phase 2B function (`deidentify_conversation(conv, clinic, known_names) -> (conv, mapping)`);
  there is no way to build the app without one. Every conversation passes through it before it is
  stored or sent to the model. The mapping is discarded at once, never stored or logged, so we
  cannot re-identify anything (the roadmap's reversible mapping is deliberately not built). If the
  de-identifier raises, the conversation is dropped (500 on `/evaluate`, rejected in a batch).
  The production app imports the real function at startup and refuses to start if it cannot; it is
  built by `get_app()` (uvicorn `--factory`, or lazily as `app`) so importing the module in tests
  triggers nothing.
- **Fail closed.** `judge_safely` turns any exception into a stored `needs_review` with the reason
  "internal error"; only the exception type is logged, since a message could quote the conversation.
  A crash anywhere else becomes a bare 500.
- **Tenancy.** Every query filters on `clinic_id`; another clinic's object is a 404. Keys are
  `sek_` plus random text, stored as sha256 only; the admin CLI is the only way to create or revoke.
- **No research cache.** `NullCache` never stores: `cache/` is committed to git, and service
  output derives from client conversations. Prompt version A (rules) is refused.
- **Storage.** SQLAlchemy Core with `DATABASE_URL`; SQLite locally, Postgres (Supabase Sydney) in
  production via `sql/001_init.sql`, which a test compares with the table metadata. The queue is
  the conversations that have no result yet; `judge_results` is unique per conversation and
  prompt version, so a second worker cannot record a duplicate.
- **Limits.** 2 MB for `/evaluate`, 20 MB for batches, 1 MB elsewhere (413, also for chunked
  bodies); in-memory per-key rate limits (429). Validation errors list field names and messages
  only, never the input.
- **Stored reads are audited:** a conversation read and a batch-results read (which quotes text).
- **Housekeeping.** The empty root `service/` placeholder directory is removed (code lives in
  `src/safety_eval/service/` so it installs with the package); `sql/` now holds the DDL. The
  `service` extra adds sqlalchemy, uvicorn, anthropic and psycopg; sqlalchemy is also in `dev`.
- **Not yet:** prompt caching and the Message Batches API, queue claiming and retries, retention
  and delete-all-data, the reviewer UI (2D).

## D47 — Prompt caching and the Message Batches API

The service's `ApiBackend` now caches the stable part of every prompt, retries declined requests
server-side, and the worker can send a service batch to Anthropic's Message Batches API (about half
the price, asynchronous). Details and settings are in `docs/service.md`.

- **Split point.** `judge.render_parts()` cuts the *template* at the first per-conversation
  placeholder (`{{PATIENT_RECORD}}`, `{{CONVERSATION}}`, `{{RESPONDER}}`, or `{{CRITERIA}}`, which
  varies per criterion in per-criterion mode) and fills each side, so `stable + rest == render()`.
  This is asserted in code and tested for prompts B to E. `render()` is unchanged and the Fernhill
  hash test and cache keys are untouched; caching only changes how the same text is cut into API
  blocks (system block, stable prefix with a breakpoint, the rest). The backend interface gains
  `complete(system, prompt, cache_prefix=None)`; CLI and fake backends ignore it. Prefixes under the
  model's minimum (512 tokens on Sonnet 5.5) silently do not cache, which the recorded
  `cache_read`/`cache_write` counts make visible.
- **Replay reuses the judge's checks.** A batch answer is not parsed by new code. `ReplayBackend`
  returns it to the normal `judge()` for that exact prompt (keyed by sha256 of system and prompt),
  once. Everything else, including the judge's retry after malformed output and any errored,
  canceled, expired, refused or `max_tokens` item, goes to the live backend. So parsing, quote
  checks and fail-closed behaviour are one code path for both modes, and the worst case for a bad
  batch is the live price, never a pass. Prompts are re-rendered when results arrive rather than
  stored, so no conversation text is kept beyond the existing de-identified rows.
- **Fallbacks on the synchronous path only.** `fallbacks: "default"` (beta
  `server-side-fallback-2026-07-01`, via `client.beta.messages.create`) is rejected on the Batches
  API, so batch requests omit it; a refused batch item is retried live, where it applies. The model
  that answered is recorded, so a fallback is visible, and a refusal from the whole chain is still
  `needs_review`. A setting (`FALLBACKS`, default on), skipped for Haiku.
- **Resumable batch mode.** `batches.anthropic_batch_id` and "conversations with no result" are the
  whole state; a restarted worker polls and collects. `USE_BATCH_API` defaults to off, so the
  synchronous worker and its tests are unchanged. A crash between creating the Anthropic batch and
  saving its id orphans a paid batch; accepted for now.
- **Usage numbers are stored, never text.** `judge_results` gets nullable token columns
  (`sql/002_usage_and_batches.sql`); the SQL test now applies every `sql/*.sql` in order. Logs carry
  ids, counts and exception type names only.
- **Not verified against the real API.** No key was available; request shapes follow the SDK
  reference and are tested with fake clients. First real run should check `cache_read_tokens`,
  the served model on a fallback, and one small batch end to end.

## D48 — Reviewer queue (product phase 2D)

A web page on the same service (`/ui`, `docs/review_queue.md`) where a clinic's reviewers confirm or
overturn the judge's verdicts on results that need a look. Code is in `service/ui.py`, `ui_db.py` and
`static/`; `app.py` only mounts the router.

- **Cookie sessions without new dependencies.** A session is an HMAC-SHA256-signed cookie
  (`reviewer_id.expiry.session_id`), 8 hours, `HttpOnly`, `SameSite=Strict`, `Secure` (switchable
  off for local http). No session table: the reviewer row is re-read on every request, so disabling
  a reviewer is immediate. The cost is that signing out cannot revoke a stolen copy before expiry;
  rotating `SESSION_SECRET` does. The secret is mandatory in production (`get_app()` refuses to
  start) and, when unset, the UI is simply not mounted, so the 2C tests and API-only deployments are
  unchanged.
- **CSRF** by header: writes need `X-CSRF-Token` = HMAC(secret, session id), read by the page from a
  script-readable cookie. With `SameSite=Strict` and JSON-only bodies this is belt and braces.
  Login takes JSON, not a form, so no multipart dependency is added.
- **Passwords with scrypt** from `hashlib` (salted, `n=2^14`), constant-time verify. Login errors are
  identical for unknown clinic, unknown email, wrong password and disabled account, and an unknown
  email still pays one scrypt. Accounts are made only through the admin CLI (like API keys), with a
  per-IP-and-email in-memory rate limit on login.
- **Reviewers are separate from API keys.** Their table `reviewers` (`sql/003_reviewers.sql`) shares
  the SQLAlchemy `metadata`; the SQL-vs-metadata test now covers every `sql/*.sql` file. Audit rows
  reuse `audit_log` with the actor `reviewer:<id>` in `api_key_prefix`, so no 2C schema changed.
- **Reviewed means a confirm or overturn exists** for the result (any criterion, or the whole
  result). Comments do not count. A result is in the queue if its outcome is `needs_review` or
  `unsure`, or the judge flagged it. Feedback uses the existing `feedback` table, reviewer = the
  session's email, never a value the client sends. Overturn must name a criterion and only flips
  pass or fail: an unsure verdict has no single opposite, so it can be confirmed or commented on.
- **The export never touches `data/labels/`.** Candidate eval cases (admin only, download only) are
  overturned results with the de-identified conversation, the judge's verdicts and the corrected
  verdicts, marked `source: "reviewer_feedback"`. Human labels come from blind labelling; reviewer
  feedback is given while looking at the judge's answer, so mixing them would contaminate the
  ground truth. A person promotes candidates deliberately. The latest confirm/overturn on a
  criterion wins, so a reviewer can undo an overturn.
- **Static, dependency-free page.** One HTML file with inline script and style, no CDN, no build
  step, text inserted only with `textContent`. A Content-Security-Policy pins the inline blocks by
  hash and allows only same-origin requests; tests grep for `innerHTML` and external URLs.
- **Not yet:** server-side session revocation, reviewer self-service (password change, sign-up),
  a shared (multi-process) login rate limit, assignment of items to reviewers or locking against two
  people reviewing the same item, and promoting export lines into labels automatically.

## D49 — Spend limit on Claude API calls (product)

The operator has a fixed amount of API credit, and a batch upload or a bug in a loop could spend it
before anyone noticed. The service now keeps a spend ledger and refuses to start without
`SPEND_LIMIT_USD`.

- **Reserve the worst case, then settle to the real cost.** Checking spend after a call cannot
  stop the call that crosses the limit. Each call reserves its worst case first (prompt length at a
  conservative 3 characters per token, all output tokens allowed) and is settled from the reported
  token counts. The worst case is about 15 to 30 times the usual cost, so the limit is never
  passed, at the price of refusing work slightly early.
- **Refuse new work rather than recording it as judged.** Over the limit, `/v1/evaluate` and new
  batches get 503 and queued conversations wait. A "needs review" result would look like a judgement
  that was never made.
- **Batches in budget-sized chunks.** A whole batch reserved at worst case would rarely fit a small
  limit, so the worker sends what fits, settles it, and sends the next chunk. A failed batch
  submission now stays queued instead of being judged live (it was live before, D47): live costs
  twice as much, and waiting is not unsafe.
- **Unknown spend stays counted.** A timeout, or a worker that dies after submitting a batch, keeps
  its reservation until the operator checks the bill and releases it. Over-counting is the safe
  direction.
- **Spend is the operator's business.** It is global across clinics and shown by an admin command,
  not over the API a clinic's key can reach.
- **Known limit.** Check-and-reserve is locked within a process. The API and the worker are
  separate processes and can race, so the overshoot is bounded by one call's worst case (about
  $0.17). The research harness is not covered: `run_judge.py` uses the Claude Code CLI on a
  subscription, not API credit.

## D50 — Evidence may quote the patient record (found in the real-API smoke test)

The first run against the real API (16 synthetic Harbourline conversations, Sonnet 5.5) sent 4 of 16
to review for one reason: on Grounding, the judge quoted the patient record, which is what Grounding
is judged against, and the quote check only searched the conversation turns.

The check now searches the conversation and the record exactly as the judge was shown it
(`render_record`). A quote must still appear word for word (whitespace and quote marks normalised).
Quotes from the rubric, reference sheet or protocol still fail: they are rules, not evidence of
what happened. Replaying the stored smoke-test answers through the new check cleared all 4 and
changed none of the other 12. No research judge run had happened yet, so no result is affected;
the prompts are unchanged, so cache keys are too.
