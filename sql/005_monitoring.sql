-- Alert dedupe state for monitoring (src/safety_eval/service/monitoring.py, decision D52).
-- Mirrors src/safety_eval/service/db.py. Run after 004_spend_ledger.sql. Codes and numbers only.
-- scope is a clinic id, or 'global' for operator-only alerts (spend). The read-only metric queries
-- are in sql/monitoring/ and need no tables of their own.

create table alert_state (
    scope         text not null,
    code          text not null,
    last_sent_at  timestamptz not null,
    last_value    double precision,
    constraint alert_state_pk primary key (scope, code)
);
