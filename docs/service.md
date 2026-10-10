# The API service (product phase 2C)

A FastAPI service in `src/safety_eval/service/`. A clinic sends conversations, and gets back the
judge's verdicts with evidence quotes, an outcome (`ok`, `unsure`, `needs_review`), and a stored
result it can review and give feedback on. It runs the same `judge()` as the research harness,
with the clinic's own rubric, reference sheet and protocol (`docs/clinics.md`).

## Run it locally

```bash
pip install -e '.[service]'          # sqlalchemy, uvicorn, anthropic, psycopg
export ANTHROPIC_API_KEY=...         # without it the service refuses to start
python -m safety_eval.service.admin create-clinic harbourline
python -m safety_eval.service.admin create-key --clinic harbourline     # prints the key once
uvicorn safety_eval.service.app:get_app --factory --port 8000           # the API; docs at /docs
python -m safety_eval.service.worker                                    # in a second terminal: batches
```

Run it from a checkout of this repository: prompts and clinic folders are read from the repo root,
as in the research harness.

The service refuses to start without `safety_eval.deid.deidentify_conversation` (Phase 2B), because
it must never run without de-identification. `uvicorn safety_eval.service.app:app` also works and
builds the same app on first use. Importing the module builds nothing, so tests need neither.

## Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./service.db` | SQLAlchemy URL. Production: Postgres (`postgresql+psycopg://...`, Supabase Sydney) |
| `ANTHROPIC_API_KEY` | none, required | the model API key |
| `MODEL` | `claude-sonnet-5-5` | model for `ApiBackend` |
| `PROMPT_VERSION` | `C` | default prompt version; version A (rules) is refused |
| `RATE_LIMIT_EVALUATE_PER_MIN` | 60 | per API key |
| `RATE_LIMIT_BATCHES_PER_MIN` | 6 | batch submissions per API key |
| `WORKER_POLL_SECONDS` | 5 | worker sleep when the queue is empty |

SQLite tables are created automatically. For Postgres, run `sql/001_init.sql` once; a test keeps
it in step with the SQLAlchemy tables.

## Authentication and admin

Every route except `/healthz` needs `Authorization: Bearer sek_...`. A key belongs to one clinic,
only its sha256 hash is stored, and revoked, unknown and malformed keys all get the same 401.
Data belonging to another clinic is a 404, never a 403.

```bash
python -m safety_eval.service.admin create-clinic harbourline   # the folder must exist in clinics/
python -m safety_eval.service.admin create-key --clinic harbourline
python -m safety_eval.service.admin revoke-key sek_AbCd1234     # the prefix shown in the audit log
```

There is deliberately no admin API; the CLI runs on the server.

## Endpoints

All under `/v1`. `KEY` is a clinic API key. Request bodies are limited to 2 MB for `evaluate`,
20 MB for batch submission and 1 MB elsewhere (413). Errors never contain conversation text.

**Evaluate one conversation.** The conversation uses the shape of `BaseConversation`, checked by
the clinic's config (`clinic.parse_conversation`); invalid input is a 422 listing the reasons.

```bash
curl -s localhost:8000/v1/evaluate -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{
  "conversation": {"conversation_id": "c-1", "responder": "assistant",
                   "patient_record": {"age_band": "25-34", "program": "anxiety support",
                                      "sessions_completed": 4, "safety_plan_on_file": "no"},
                   "turns": [{"speaker": "patient", "text": "..."}, {"speaker": "responder", "text": "..."},
                             {"speaker": "patient", "text": "..."}, {"speaker": "responder", "text": "..."}]},
  "known_names": ["Jo Citizen"], "prompt_version": "C"}'
```

Returns `{"result_id", "conversation_pk", "result": {outcome, reasons, output: {criteria, ...}, ...}}`.
`known_names` (optional) are names the de-identifier must remove; `prompt_version` is optional.
A model failure, bad answer or any unexpected error is stored and returned as `needs_review`
(reason "internal error" for unexpected ones), never `ok`. Over 60 requests a minute per key is a 429.

**Batches.** Up to 1000 conversations; each is validated, invalid ones come back as `rejected`
with reasons, and valid ones are de-identified, stored and queued for the worker (202).
`known_names` applies to every conversation in the batch.

```bash
curl -s localhost:8000/v1/batches -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"conversations": [ {...}, {...} ]}'
curl -s "localhost:8000/v1/batches/batch_0123abcd?offset=0&limit=50" -H "Authorization: Bearer $KEY"
```

The status (`queued`, `running`, `done`) is `done` once every accepted conversation has a result.
The response also has counts by outcome, the number flagged, and the paginated results.

**Feedback.** A reviewer confirms, overturns or comments on a result (404 if it is not the
caller's clinic's):

```bash
curl -s localhost:8000/v1/feedback -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"result_id": 12, "reviewer": "Dr Test", "action": "overturn", "criterion": "safety", "note": "..."}'
```

**Stats.** Daily counts for the last `days` (1 to 365, default 28, UTC days): evaluated, flagged,
unsure, needs_review, and failed verdicts per criterion.

```bash
curl -s "localhost:8000/v1/clinics/me/stats?days=28" -H "Authorization: Bearer $KEY"
```

**Read a stored conversation** (`GET /v1/conversations/{pk}`) returns the de-identified text and
writes an `audit_log` row. Reading batch results, which quote the conversation, is audited too.

`GET /healthz` checks the database and needs no key.

## What is stored

Tables: clinics, api_keys, batches, conversations, judge_results, feedback, audit_log
(`sql/001_init.sql`). **Only de-identified conversations are stored.** Each conversation passes
through the de-identifier before it is stored or sent to the model. The de-identifier returns a
placeholder-to-original mapping; the service throws it away immediately, so it is never stored
and never logged. (The roadmap's reversible mapping in the customer's tenant store is not built:
without it nobody can re-identify a conversation from our data, which is the safer default.)
Evidence quotes in results come from the de-identified text the judge saw.

Logs carry ids, counts and outcomes only: never conversation text, record values, model output
or exception messages (only exception type names). A test captures logs through the evaluate,
batch and failure flows to check this. The service does not use the research response cache
(`cache/`, which is committed to the repo); every call goes to the model.

## Not done yet

- **Anthropic prompt caching** on the stable prefix (system prompt, rubric, protocol, reference)
  and the **Message Batches API** for `/v1/batches` (about half the cost). Today batches are
  judged one call at a time by the worker, and `ApiBackend` makes no retries beyond the judge's
  one malformed-output retry; any failure is `needs_review`.
- **Queue hardening.** The queue is "conversations with no result yet", for one worker. Claiming
  rows (`FOR UPDATE SKIP LOCKED`), retries with backoff and stuck-batch recovery come later.
- **Rate limiting is in memory**, per process.
- **Retention and delete-all-data** (roadmap 2G), `docs/security.md`, per-conversation
  `known_names` in a batch, and Postgres migrations beyond the single init file.
- **Reviewer UI** is phase 2D; this phase only stores feedback.
