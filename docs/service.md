# The API service (product phase 2C)

A FastAPI service in `src/safety_eval/service/`. A clinic sends conversations, and gets back the
judge's verdicts with evidence quotes, an outcome (`ok`, `unsure`, `needs_review`), and a stored
result it can review and give feedback on. It runs the same `judge()` as the research harness,
with the clinic's own rubric, reference sheet and protocol (`docs/clinics.md`).

## Run it locally

```bash
pip install -e '.[service]'          # sqlalchemy, uvicorn, anthropic, psycopg
export ANTHROPIC_API_KEY=...         # without it the service refuses to start
export SPEND_LIMIT_USD=20            # nor without a spend limit (below)
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
| `JUDGE_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY` | none, required | the model API key; the first name wins. In a Claude Code cloud session use `JUDGE_ANTHROPIC_API_KEY`, because `ANTHROPIC_API_KEY` is reserved for Claude Code itself there |
| `JUDGE_ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | API endpoint; `ANTHROPIC_BASE_URL` is deliberately ignored, because Claude Code sessions set it for their own traffic |
| `SPEND_LIMIT_USD` | none, required | total Claude API spend allowed, all clinics together (below) |
| `MODEL` | `claude-sonnet-5-5` | model for `ApiBackend` |
| `PROMPT_VERSION` | `C` | default prompt version; version A (rules) is refused |
| `RATE_LIMIT_EVALUATE_PER_MIN` | 60 | per API key |
| `RATE_LIMIT_BATCHES_PER_MIN` | 6 | batch submissions per API key |
| `WORKER_POLL_SECONDS` | 5 | worker sleep when the queue is empty (or while waiting on Anthropic) |
| `EFFORT` | unset | `output_config.effort` (`low` to `max`), sent only when set |
| `FALLBACKS` | `true` | server-side refusal fallback on the synchronous path (below) |
| `USE_BATCH_API` | `false` | the worker sends service batches to the Message Batches API (below) |

SQLite tables are created automatically. For Postgres, run every file in `sql/` in order
(`001_init.sql` to `005_monitoring.sql`; the read-only views in `sql/monitoring/` are optional, see `docs/monitoring.md`); a test applies every file in `sql/` in order and checks the result
against the SQLAlchemy tables.

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
(`sql/001_init.sql`, `sql/002_usage_and_batches.sql`). **Only de-identified conversations are stored.** Each conversation passes
through the de-identifier before it is stored or sent to the model. The de-identifier returns a
placeholder-to-original mapping; the service throws it away immediately, so it is never stored
and never logged. (The roadmap's reversible mapping in the customer's tenant store is not built:
without it nobody can re-identify a conversation from our data, which is the safer default.)
Evidence quotes in results come from the de-identified text the judge saw.

Logs carry ids, counts and outcomes only: never conversation text, record values, model output
or exception messages (only exception type names). A test captures logs through the evaluate,
batch and failure flows to check this. The service does not use the research response cache
(`cache/`, which is committed to the repo); every call goes to the model (Anthropic's prompt caching, above, is what saves cost).

## Prompt caching, refusal fallbacks and batch mode (D47)

**Prompt caching (always on for `ApiBackend`).** Each call sends the system prompt as one text
block and the user message as two: the *stable prefix* (the prompt template up to the first
per-conversation placeholder: rubric, reference sheet, protocol, instructions) and the rest
(record, conversation). The system block and the prefix carry `cache_control: ephemeral`, so
repeated calls read them from Anthropic's cache. The text the model sees is unchanged. Prefixes
shorter than the model's minimum (512 tokens on Sonnet 5.5) silently do not cache. Check it works
in production: `cache_read_tokens` on `judge_results` should be large after the first call in
any five-minute window (the default TTL; a quiet service pays the write premium again).
Cost: a cache read costs about a tenth of fresh input, a cache write about 1.25 times.

**Refusal fallbacks (`FALLBACKS`, default on).** The synchronous path sends the request with
`client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default")`, so
a declined request (for example the `cyber` category) is re-run server-side on the model Anthropic
recommends. The model that answered is recorded in the result (`model`), so a fallback is visible.
If the whole chain still refuses, or the model stops at `max_tokens`, the call fails and the
conversation is `needs_review`. Turn it off with `FALLBACKS=false`; it is skipped automatically for
Haiku models, which have no server-side fallback.

**Message Batches API (`USE_BATCH_API=true`, default off).** About half the price of synchronous
calls, but asynchronous (usually under an hour, at most 24). With it on, the worker does this on each
pass, and keeps all state in the database so a restart loses nothing:

1. For a service batch with no `anthropic_batch_id`, it renders every conversation's first-attempt
   prompts (one per conversation, or five for a per-criterion prompt), submits them as one Anthropic
   batch (`custom_id` is `c<conversation pk>-<criterion or all>`), and stores the id on the
   `batches` row. The batch status stays `running`.
2. For a batch with an id, it polls. When Anthropic reports `ended`, it collects the results (keyed by
   `custom_id`, any order) and judges each conversation with the normal `judge()` through a
   `ReplayBackend`: the batch answer is used for that exact prompt, so parsing, the quote checks and
   fail-closed handling are the same code as the synchronous path. The judge's one retry after a
   malformed answer, and any item that was errored, canceled, expired, refused, or `max_tokens`,
   gets a live call instead (so the usual price, and fallbacks if on). A live failure is `needs_review`.
3. A batch whose submission is refused by the API is judged live instead, so it still finishes.

`python -m safety_eval.service.worker --once` makes one pass in this mode and exits; run it again
(or keep the worker running) to collect. Batch requests carry the same cached blocks as live ones, but
cache hits inside a batch are best effort. The `fallbacks` parameter is rejected on the Batches API,
so it is never sent there. Limits: a worker that dies between creating the Anthropic batch and
writing its id to the database leaves an orphan batch (paid for, never collected) and the
conversations are submitted again.

**Token usage.** `judge_results` has nullable `input_tokens`, `output_tokens`, `cache_read_tokens`
and `cache_write_tokens`: numbers summed over the calls behind that result, for cost reporting.
They are NULL when the backend reports none. Text is never stored.

## Spend limit (D49)

The service never sends a Claude API call that could take total spend past `SPEND_LIMIT_USD`, and
will not start without one.

- **Before** each call, its worst case is reserved: the prompt at 3 characters per token at the
  full input rate, plus every allowed output token. About $0.34 per conversation on Sonnet 5.5 with
  prompt C (one retry included), $0.08 per conversation in a batch. **After** the call, the
  reservation is replaced by the real cost from the reported token counts, typically a cent or two.
- When the next conversation does not fit, `/v1/evaluate` returns **503** ("spend limit
  reached"), new batches are refused with 503, and queued conversations stay queued. Nothing is
  marked as judged.
- In batch mode the worker sends as many conversations as fit at worst case, settles that chunk at
  its real cost when it ends, then sends the next. A failed submission stays queued (it is not
  judged live at twice the price).
- A refusal is billed and costed; an HTTP error response is not billed and settles to zero; a
  timeout keeps its reservation, because the real cost is unknown.
- A warning is logged at 80%.
- Spend is the operator's, not a clinic's, so it is shown on the server only:
  `python -m safety_eval.service.admin spend`. After checking the Anthropic Console bill,
  `admin release-reservations` clears reservations that were never settled.

Set a spend limit in the Anthropic Console as well: this guard covers the service, not other
uses of the same key. The rates in `budget.PRICES` are October 2026 list prices; check them
when Anthropic changes pricing, and add a model there before using it (startup refuses an
unpriced model).

## Not done yet

- **Retries.** `ApiBackend` makes no retries beyond the SDK's own and the judge's one
  malformed-output retry; any failure is `needs_review`.
- **Queue hardening.** The queue is "conversations with no result yet", for one worker. Claiming
  rows (`FOR UPDATE SKIP LOCKED`), retries with backoff and stuck-batch recovery come later.
- **Rate limiting is in memory**, per process.
- **Retention and delete-all-data** (roadmap 2G), `docs/security.md`, per-conversation
  `known_names` in a batch, and Postgres migrations beyond the single init file.
- **Reviewer UI** is phase 2D; this phase only stores feedback.
