# Product roadmap: from eval harness to production startup

Goal: turn this research harness into a product that Australian (then global) digital-health
companies pay for: **safety QA for patient-facing conversations**. A customer sends chat
transcripts plus their own protocol, and gets back flagged conversations with evidence, a review
queue, drift monitoring and a regression gate for their AI assistant.

This file is written so Claude Code can execute it. Tasks marked **[HUMAN]** are Justin's and
must not be done by Claude Code. Everything else is a code task with acceptance criteria.

---

## 0. Rules for Claude Code (read first)

1. **The research harness stays intact.** Everything in `src/safety_eval/` keeps working and
   `pytest` stays green after every task. The Fernhill clinic becomes the first "clinic config"
   and the reference fixture, not something deleted.
2. **Never fill in, edit or invent a human label.** Labels come only from Justin
   (`labelling import-web`). See D43.
3. **Never bypass the test-set lock** in `run_judge.py` or edit
   `data/labels/test_run_authorisation.json`.
4. **The judge fails closed everywhere**, including in the service: a failed call, a bad JSON
   answer, or a quote that is not in the conversation is `needs_review`, never `pass`.
5. **No real patient data in the repo, in logs, in test fixtures or in error messages.**
6. Record every non-obvious product decision in `docs/decisions.md` (continue numbering from D43).
7. Work on a branch per phase (`product/phase-2a-clinic-config`, etc.), small commits, tests with
   every change. Ask before any spend above the existing 100-call confirmation.

---

## Critical path (fastest route, about 6–8 weeks)

```
Week 1-2   [HUMAN] labelling + judge results    |  [CODE] 2A clinic config, 2B ingestion
Week 2-4   [HUMAN] 10 customer conversations    |  [CODE] 2C API service, 2D review queue
Week 4-6   [HUMAN] ABN, landing page, adviser   |  [CODE] 2E gate, 2F monitoring, 2G security
Week 6-8   [HUMAN] first design-partner audit   |  [CODE] 2H deploy, demo clinic, docs
           [HUMAN] incorporate if needed, apply to Claude for Startups / accelerator
```

The two tracks run in parallel. The application is strongest once Phase 1 results and a
deployed demo both exist.

---

## Phase 1: prove the judge works (blocks the pitch)

**[HUMAN]**
- Finish labelling pass 1 on the web page (`python -m safety_eval.label_page`), then the
  relabel pass for self-consistency.
- Check the drafted reasoning for the prompt-D examples.
- Choose the final prompt on dev and write `test_run_authorisation.json`.

**[CODE]**
- 1.1 Run `python -m safety_eval.run_judge` for A–E on dev, then `python -m safety_eval.compare`.
- 1.2 After Justin authorises, run the chosen version and baseline A on test, plus the
  consistency repeats.
- 1.3 Write `reports/` metrics and `docs/results_summary.md`: headline recall on high-severity
  cases with intervals, false-alarm rate, per-criterion table, HealthBench physician ceiling next
  to it, and an error analysis of every miss.
- 1.4 Write `docs/one_pager.md` (public-safe): problem, approach, the headline numbers, limits.

**Done when:** the README status table shows a real number that exists in `reports/`.

---

## Phase 2: productise (code)

### 2A. Clinic configuration (multi-tenant core) — do first

Today Fernhill is hard-wired: `reference.py` reads fixed files, `judge.py` has fixed `RUBRIC`
and `STAFF`, `schemas.PatientRecord` assumes semaglutide (`MEDICATION`, `dose_step`).

- Create `clinics/<clinic_id>/` holding `clinic.yaml` (name, responder roles, enabled criteria,
  escalation contacts), `protocol.md`, `reference.md` (facts), and optional `rubric.md` overrides.
- Move Fernhill to `clinics/fernhill/` (or have its `clinic.yaml` point at `data/reference/`).
- New `src/safety_eval/clinic.py`: `Clinic.load(id)` returns protocol text, facts, rubric,
  staff labels and the record schema.
- Make the patient record generic: a validated free-form record (`dict[str, str | int | list]`)
  per clinic, with `PatientRecord` kept as Fernhill's typed schema.
- Thread `clinic` through `judge.render`, `judge.judge`, `run_judge`, `baseline_rules` (rules
  that are Fernhill-specific stay Fernhill-only).
- Add a second, very different fixture clinic (for example a fictional mental-health text
  service) to prove nothing is hard-coded.

**Acceptance:** for Fernhill, the rendered prompt is byte-identical to before, so every cache key
is unchanged (add a test that renders a few conversations and compares to stored hashes). The
second clinic renders with no Fernhill text in it (reuse `leakage.py`-style checks).

### 2B. Ingestion and de-identification

- `src/safety_eval/ingest/`: importers for the canonical JSON format, CSV (one row per turn), and
  a mapping-based adapter so new formats (Intercom, Zendesk, Freshdesk exports) are config, not
  code.
- `src/safety_eval/deid.py`: strip or mask names, emails, phones, street addresses, dates of
  birth, Medicare and IHI numbers, and URLs before anything is stored or sent to a model.
  Keep a reversible mapping only in the customer's tenant store, never in logs.
- The evidence-quote check must run on the de-identified text the judge saw.

**Acceptance:** tests with synthetic PII in every field type; nothing matching the PII patterns
survives; the quote check still passes on de-identified conversations.

### 2C. API service (`service/`)

FastAPI app, matching the README's production table.

- `POST /v1/evaluate` — one conversation in, `JudgeResult` out (verdicts, evidence, outcome).
- `POST /v1/batches` and `GET /v1/batches/{id}` — bulk upload, async processing, results.
- `POST /v1/feedback` — a reviewer confirms or overturns a verdict.
- `GET /v1/clinics/{id}/stats` — flag rates over time.
- Auth: per-clinic API keys (hashed at rest), every query scoped by `clinic_id`.
- Storage: Postgres (Supabase in the Sydney region, `ap-southeast-2`) with migrations in `sql/`.
  Tables: clinics, api_keys, conversations (de-identified), judge_results, feedback, audit_log.
- Model calls: production default is `ApiBackend` (add `anthropic` as an optional `[service]`
  dependency). Keep `CliBackend` for research. Add:
  - prompt caching on the stable prefix (system prompt, rubric, protocol, reference);
  - the Message Batches API for `/v1/batches` (about half the cost);
  - retries with backoff; any final failure is `needs_review`.
- Background worker for batches (simple Postgres-backed queue first; no extra infrastructure).

**Acceptance:** API tests with `FakeBackend` cover auth, tenant isolation (clinic A can never
read clinic B), fail-closed outcomes, and batch lifecycle. OpenAPI docs render.

### 2D. Review queue (web)

- Grow `label_page.py` / `labelling/template.html` into a reviewer app served by the service:
  a queue of `fail`, `unsure` and `needs_review` conversations, highlighted evidence quotes,
  confirm / overturn / comment buttons that call `/v1/feedback`.
- Login for reviewers (Supabase Auth or magic links), roles: admin, reviewer.
- Overturned verdicts become candidate eval cases for that clinic (export to a JSONL file in
  the same shape as `data/labels/`).

**Acceptance:** a reviewer can clear a 20-item queue end to end against the demo clinic.

### 2E. Regression gate (a product on its own)

- `safety-eval gate --clinic X --baseline run1 --candidate run2` CLI: compares recall on
  high-severity cases and false alarms, exits non-zero if recall drops beyond a set margin.
- A reusable GitHub Action wrapping it, so a customer's prompt or model change runs against
  their eval set in CI. Build on `promptfoo/` where it helps.

**Acceptance:** an example workflow in `examples/` fails on a deliberately worse candidate.

### 2F. Monitoring

- SQL in `sql/`: daily flag rate per criterion, unsure rate, needs_review rate, reviewer
  overturn rate, latency and cost per conversation.
- Alert bands (for example flag rate more than 2 standard deviations from the 28-day mean) with a
  webhook or email notification.
- A simple dashboard page in the reviewer app.

### 2G. Security, privacy and compliance (code side)

- Data stored in Australia only; TLS everywhere; encryption at rest (managed by the host).
- Audit log of every read of conversation content.
- Retention setting per clinic, with automatic deletion; a delete-all-data endpoint.
- Logs and error tracking never contain conversation text (add a test that greps captured logs).
- Rate limiting and request size limits.
- Dependency and secret scanning in CI.
- `docs/security.md`: data flow diagram, subprocessors (host, Supabase, Anthropic), retention.
  Customers will ask for this in every sales call.

### 2H. Deploy and demo

- `Dockerfile`, `docker-compose.yml` for local, and deployment to a host with a Sydney region.
- CI (GitHub Actions): `pytest`, lint, type check, migrations check on every push.
- A public demo clinic with synthetic data only, so prospects can try it without signing up.
- `docs/quickstart.md`: send a first conversation in five minutes.

**Phase 2 done when:** a new clinic can be onboarded with config only, send transcripts through
the API, review flags in the web app, and see monitoring, all on a deployed URL, with every test
green.

---

## Phase 3: customers **[HUMAN]**

- List 30 targets: Australian telehealth and weight-management services, mental-health text
  services, pharmacy chat, and companies selling AI patient assistants (often faster buyers than
  clinics).
- Have 10 problem interviews. What do they check today? Who gets in trouble when the bot says
  something wrong? What would they pay?
- Offer a **free safety audit** to 2–3 design partners: they send de-identified transcripts, you
  return a flagged report. Get a written pilot agreement or letter of intent.
- Recruit a clinician adviser (GP or pharmacist) to review rubrics; this fixes the "one
  non-clinician labeller" limitation.
- Pricing hypothesis to test: per-conversation evaluated plus a platform fee; the regression gate
  as a cheaper entry product for AI vendors.
- Landing page with the one-pager, demo link and a contact form.

---

## Phase 4: company, legal and funding in Australia **[HUMAN]**

Spend nothing until it is needed.

| Step | Cost | When |
|---|---|---|
| ABN as a sole trader (abr.gov.au) | Free | Now. Lets you invoice and look legitimate. |
| Business name registration (ASIC) | Small fee | When you pick the name. |
| Check whether Claude for Startups accepts a sole trader or unincorporated founder | Free | Now: ask Anthropic before paying for anything. |
| Pty Ltd company (ASIC) | About A$600, plus a yearly review fee | Only if a program, investor or customer requires it, or an accelerator does it for you. |
| Separate business bank account | Usually free | With the ABN. |
| GST registration | Free | Required once turnover reaches A$75k. |
| Privacy policy and terms of service, data processing agreement for customers | Templates first, lawyer before first paid contract | Before the first pilot with real data. |
| Professional indemnity and cyber insurance | Varies | Before the first paid customer. |
| R&D Tax Incentive (refundable offset for small companies) | Free to register | Needs a company; register each year's R&D activities. |
| Regulatory check: position as quality assurance and review triage, **not** clinical decision support, to stay outside TGA software-as-a-medical-device rules | One short legal consult | Before marketing claims go public. |

Funding and credits:
- Apply to **Claude for Startups** once Phase 1 results and the deployed demo exist. Say plainly
  what credits pay for: validation runs at scale and free audits for design partners.
- Accelerators: Startmate, Antler, and university incubators. They can provide a partner referral
  for higher credit tiers and often handle incorporation.
- Also check Anthropic's other programs (for example AI for Science) in case the validation
  research fits.

---

## Definition of "production ready"

- [ ] Published validation results with the method behind them (Phase 1)
- [ ] Any clinic onboarded by config, no code changes (2A)
- [ ] PII removed before storage or model calls (2B)
- [ ] Authenticated, tenant-isolated API with fail-closed judging (2C)
- [ ] Reviewer queue with feedback loop (2D)
- [ ] Regression gate usable in a customer's CI (2E)
- [ ] Monitoring and alerts (2F)
- [ ] Australian data residency, audit log, retention, security doc (2G)
- [ ] Deployed, with CI, a demo clinic and a quickstart (2H)
- [ ] At least one design partner using it on real (de-identified) data (Phase 3)
- [ ] ABN or company, privacy policy, terms, insurance (Phase 4)

---

## Prompt to give Claude Code

> Read `docs/product_roadmap.md`, `README.md` and `docs/decisions.md`. Follow section 0 strictly.
> Start Phase 2A on a new branch `product/phase-2a-clinic-config`. Keep Fernhill's rendered
> prompts byte-identical and all tests green. Then continue with 2B and 2C in order, one branch
> each, committing as you go. Stop and ask me before any paid model calls or anything marked
> [HUMAN].
