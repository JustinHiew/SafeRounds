-- Reviewer accounts for the review queue UI (product phase 2D).
-- Mirrors src/safety_eval/service/ui_db.py; tests/test_service_api.py checks every sql/*.sql file
-- against the SQLAlchemy tables.
-- password_hash is 'scrypt$n$r$p$salt$hash' (base64); the password itself is never stored.

create table reviewers (
    id             integer generated always as identity primary key,
    clinic_id      text not null references clinics (id),
    email          text not null,
    role           text not null,
    password_hash  text not null,
    created_at     timestamptz not null,
    disabled_at    timestamptz,
    unique (clinic_id, email),
    constraint reviewers_role_check check (role in ('admin', 'reviewer'))
);
