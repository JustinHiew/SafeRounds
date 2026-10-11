-- Spend ledger for the Claude API budget guard (src/safety_eval/service/budget.py, decision D49).
-- Mirrors src/safety_eval/service/db.py. Run after 003_reviewers.sql. Numbers only, never text.

create table spend_ledger (
    id                          integer generated always as identity primary key,
    created_at                  timestamptz not null,
    settled_at                  timestamptz,
    kind                        text not null,
    model                       text not null,
    ref                         text,
    usd                         double precision not null,
    settled                     boolean not null,
    input_tokens                integer,
    output_tokens               integer,
    cache_creation_input_tokens integer,
    cache_read_input_tokens     integer,
    constraint spend_ledger_kind_check check (kind in ('call', 'batch'))
);
create index spend_ledger_open_idx on spend_ledger (settled) where not settled;
