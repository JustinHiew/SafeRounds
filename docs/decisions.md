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
