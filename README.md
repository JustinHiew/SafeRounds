# SafeRounds

**Retrospective safety audits for patient-facing chat.** [saferounds.dev](https://saferounds.dev)

SafeRounds uses Claude to review past conversations between patients and a health service's AI
assistant or support staff. Each staff reply is checked against the service's own protocol and the
patient's record, and every finding comes with the exact words behind it, so a person can confirm
or overturn it in minutes. Anything the model is unsure about, or any answer it cannot back with a
quote, goes to a person instead of passing.

It reviews conversations after they have happened. It does not sit between a service and its
patients, and it does not check or block replies before they are sent.

> Working prototype, seeking Australian design partners for free pilot audits.
> All data in this repository is synthetic. SafeRounds is a quality-assurance tool, not medical
> advice or clinical decision support.

## Features

| Feature | What it does | Status |
|---|---|---|
| Protocol-based review | Five checks per reply (Safety, Escalation, Accuracy, Scope, Grounding), each judged against the service's own protocol, reference sheet and patient record | Working |
| Quoted evidence | Every verdict cites the reply and the rule it broke; a quote that is not found in the conversation or record sends the result to a person | Working |
| Fails closed | A failed model call, an unreadable answer or an "unsure" check becomes `needs_review`, never a pass | Working |
| De-identification | Names, contact details, dates and record numbers replaced before anything is stored or sent for review | Working |
| Import | CSV, JSON or a mapped helpdesk export, validated against the service's config, with a rejection report | Working |
| Review queue | Web app where reviewers confirm or overturn each flag, with an audit record every time a conversation is opened | Working |
| API service | `POST /v1/evaluate` and batch jobs, per-service API keys, strict tenant isolation | Working |
| Spend limit | Worst-case cost reserved before each model call, so total spend never passes the set limit | Working |
| Cost controls | Prompt caching of the protocol, and the Message Batches API for large audits at half price | Working |
| Monitoring and alerts | Daily flag rates per service, alerts when they drift, an admin dashboard | Built, not yet running for a service |
| Regression gate | GitHub Action that blocks a prompt or model change if it catches fewer serious problems | Built, not yet running for a service |
| Corrections into test cases | Flags that reviewers overturn are exported as test cases for the regression gate | Built, not yet running for a service |
| Hosted service in Sydney | Managed deployment with Australian data residency | Planned |
| Independent validation | Measured miss rate, false alarms and reviewer disagreement on a labelled set | In progress |

## How well does it work?

Not measured yet, and no number will appear here before it exists in `reports/`.

- **Cost:** about US$0.015 per four-message conversation on Claude Sonnet 5.5 (US$0.007 with the
  batch API), measured on 11 October 2026.
- **Validation in progress:** 250 synthetic conversations, written by three different models, with
  planted failures, hard negatives and normal cases, split so near-duplicates never cross train,
  dev and test. Prompt versions are compared on dev and the chosen one runs once on the locked test
  set. The headline metric is recall on high-severity cases, fixed in
  [`docs/analysis_plan.md`](docs/analysis_plan.md) before any result exists.
- **Physician ceiling:** agreement between two physicians on emergency referrals in the public
  HealthBench set ([`reports/healthbench_physician_agreement.json`](reports/healthbench_physician_agreement.json))
  is the ceiling any judge is compared against.

## Run it

```bash
pip install -e ".[dev]"
pytest                                    # 400+ tests, no model access needed
```

The service, with a real model:

```bash
pip install -e ".[service]"
export JUDGE_ANTHROPIC_API_KEY=...         # or ANTHROPIC_API_KEY
export SPEND_LIMIT_USD=20                 # required: the service will not start without it
python -m safety_eval.service.admin create-clinic harbourline
python -m safety_eval.service.admin create-key --clinic harbourline
uvicorn safety_eval.service.app:get_app --factory --port 8000
```

See [`docs/service.md`](docs/service.md) for every setting.

## Documentation

| Topic | Doc |
|---|---|
| API service, settings, authentication | [`docs/service.md`](docs/service.md) |
| Per-service configuration (protocol, rubric, record fields) | [`docs/clinics.md`](docs/clinics.md) |
| Import and de-identification | [`docs/ingest.md`](docs/ingest.md) |
| Review queue | [`docs/review_queue.md`](docs/review_queue.md) |
| Monitoring and alerts | [`docs/monitoring.md`](docs/monitoring.md) |
| Regression gate | [`docs/regression_gate.md`](docs/regression_gate.md) |
| The five checks | [`docs/rubric.md`](docs/rubric.md) |
| Evaluation data and what it can show | [`docs/datasheet.md`](docs/datasheet.md) |
| Every design decision, with its reason | [`docs/decisions.md`](docs/decisions.md) |

## Limits

- **Synthetic data only.** Both services in this repo, Fernhill Weight Health and Harbourline
  Support, are fictional, and every conversation and patient record is invented.
- **Results on real conversations would likely be worse.** Real patients and staff write in ways
  a synthetic set does not capture.
- **Evaluation labels come from one non-clinician** until clinical reviewers are involved.
- **Automatic de-identification is pattern-based** and can miss names and unusual details. Pilot
  partners de-identify transcripts before sharing them; see [saferounds.dev/privacy](https://saferounds.dev/privacy).

## Layout

```
src/safety_eval/          judge, checks, de-identification, import, regression gate
src/safety_eval/service/  API, worker, spend limit, review queue, monitoring
clinics/                  per-service configs: fernhill and harbourline
prompts/                  versioned judge prompts
data/                     synthetic conversations, reference material, splits
sql/                      Postgres migrations and monitoring views
examples/                 regression-gate workflow for a customer's repo
tools/                    smoke test and UI checks
site/                     saferounds.dev
docs/                     design docs and decision log
tests/                    pytest suite
```

## Contact

Justin Hiew, founder: [justin@saferounds.dev](mailto:justin@saferounds.dev).
SafeRounds is the trading name of Justin Hiew, sole trader, Australia.
