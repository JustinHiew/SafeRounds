-- Token usage per judged conversation (numbers only, never text) and the Anthropic Message Batches
-- API id of a service batch. Mirrors src/safety_eval/service/db.py. Run after 001_init.sql.
-- NULL usage means no API usage was recorded (for example the model ran through the CLI backend).

alter table judge_results add column input_tokens       integer;
alter table judge_results add column output_tokens      integer;
alter table judge_results add column cache_read_tokens  integer;
alter table judge_results add column cache_write_tokens integer;

alter table batches add column anthropic_batch_id text;
