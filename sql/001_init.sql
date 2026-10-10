-- Postgres schema for the API service (Supabase, Sydney region ap-southeast-2).
-- Mirrors src/safety_eval/service/db.py; tests/test_service_api.py checks table and column names.
-- Conversations are stored de-identified only.

create table clinics (
    id          text primary key,
    name        text not null,
    created_at  timestamptz not null
);

create table api_keys (
    id          integer generated always as identity primary key,
    clinic_id   text not null references clinics (id),
    key_hash    text not null unique,   -- sha256 of the key; the key itself is never stored
    prefix      text not null unique,   -- first characters of the key, for display and revoke-key
    created_at  timestamptz not null,
    revoked_at  timestamptz
);

create table batches (
    id              text primary key,
    clinic_id       text not null references clinics (id),
    status          text not null,
    prompt_version  text not null,
    n_submitted     integer not null,
    n_rejected      integer not null,
    created_at      timestamptz not null,
    finished_at     timestamptz,
    constraint batches_status_check check (status in ('queued', 'running', 'done'))
);

create table conversations (
    pk               integer generated always as identity primary key,
    clinic_id        text not null references clinics (id),
    conversation_id  text not null,
    batch_id         text references batches (id),
    deidentified     jsonb not null,
    created_at       timestamptz not null,
    unique (clinic_id, conversation_id, batch_id)
);
create index conversations_batch_idx on conversations (batch_id);

create table judge_results (
    pk               integer generated always as identity primary key,
    clinic_id        text not null references clinics (id),
    conversation_pk  integer not null references conversations (pk),
    prompt_version   text not null,
    model            text not null,
    outcome          text not null,
    flagged          boolean not null,
    severity         text,
    result           jsonb not null,
    created_at       timestamptz not null,
    unique (conversation_pk, prompt_version),
    constraint judge_results_outcome_check check (outcome in ('ok', 'unsure', 'needs_review'))
);
create index judge_results_clinic_time_idx on judge_results (clinic_id, created_at);

create table feedback (
    pk          integer generated always as identity primary key,
    clinic_id   text not null references clinics (id),
    result_pk   integer not null references judge_results (pk),
    reviewer    text not null,
    criterion   text,
    action      text not null,
    note        text not null,
    created_at  timestamptz not null,
    constraint feedback_action_check check (action in ('confirm', 'overturn', 'comment'))
);

create table audit_log (
    pk              integer generated always as identity primary key,
    clinic_id       text not null references clinics (id),
    api_key_prefix  text not null,
    action          text not null,
    object_id       text not null,
    created_at      timestamptz not null
);
