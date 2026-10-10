"""Tables (SQLAlchemy Core) and the few writes shared by the app, the worker and the admin CLI.

Postgres (Supabase, Sydney) is the production target; sql/001_init.sql is the same schema as DDL
and a test keeps the two in step. Conversations are stored de-identified only. Queries on
tenant data always filter on clinic_id.
"""

import json
import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
Json = sa.JSON().with_variant(JSONB(), "postgresql")
Time = sa.DateTime(timezone=True)

clinics = sa.Table(
    "clinics", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", Time, nullable=False),
)

api_keys = sa.Table(
    "api_keys", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("key_hash", sa.Text, nullable=False, unique=True),     # sha256 hex; the key is never stored
    sa.Column("prefix", sa.Text, nullable=False, unique=True),       # shown in the audit log and revoke-key
    sa.Column("created_at", Time, nullable=False),
    sa.Column("revoked_at", Time),
)

batches = sa.Table(
    "batches", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("prompt_version", sa.Text, nullable=False),
    sa.Column("n_submitted", sa.Integer, nullable=False),    # accepted and queued
    sa.Column("n_rejected", sa.Integer, nullable=False),
    sa.Column("created_at", Time, nullable=False),
    sa.Column("finished_at", Time),
    sa.CheckConstraint("status in ('queued', 'running', 'done')", name="batches_status_check"),
)

conversations = sa.Table(
    "conversations", metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("batch_id", sa.Text, sa.ForeignKey("batches.id")),
    sa.Column("deidentified", Json, nullable=False),
    sa.Column("created_at", Time, nullable=False),
    sa.UniqueConstraint("clinic_id", "conversation_id", "batch_id"),
    sa.Index("conversations_batch_idx", "batch_id"),
)

judge_results = sa.Table(
    "judge_results", metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("conversation_pk", sa.Integer, sa.ForeignKey("conversations.pk"), nullable=False),
    sa.Column("prompt_version", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("flagged", sa.Boolean, nullable=False),
    sa.Column("severity", sa.Text),
    sa.Column("result", Json, nullable=False),               # the JudgeResult
    sa.Column("created_at", Time, nullable=False),
    # Also stops two workers from both recording a result for the same conversation.
    sa.UniqueConstraint("conversation_pk", "prompt_version"),
    sa.CheckConstraint("outcome in ('ok', 'unsure', 'needs_review')", name="judge_results_outcome_check"),
    sa.Index("judge_results_clinic_time_idx", "clinic_id", "created_at"),
)

feedback = sa.Table(
    "feedback", metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("result_pk", sa.Integer, sa.ForeignKey("judge_results.pk"), nullable=False),
    sa.Column("reviewer", sa.Text, nullable=False),
    sa.Column("criterion", sa.Text),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("note", sa.Text, nullable=False),
    sa.Column("created_at", Time, nullable=False),
    sa.CheckConstraint("action in ('confirm', 'overturn', 'comment')", name="feedback_action_check"),
)

audit_log = sa.Table(
    "audit_log", metadata,
    sa.Column("pk", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("api_key_prefix", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("object_id", sa.Text, nullable=False),
    sa.Column("created_at", Time, nullable=False),
)


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(dt):
    """SQLite hands back naive datetimes; Postgres hands back aware ones. Both mean UTC here."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def make_engine(url):
    engine = sa.create_engine(url, pool_pre_ping=True)
    if url.startswith("sqlite"):
        metadata.create_all(engine)   # local convenience; Postgres is created from sql/001_init.sql
    return engine


def new_batch_id():
    return "batch_" + secrets.token_hex(8)


def insert_conversation(conn, clinic_id, conv, batch_id=None):
    """Store a de-identified conversation; returns its pk."""
    return conn.execute(conversations.insert().values(
        clinic_id=clinic_id, conversation_id=conv.conversation_id, batch_id=batch_id,
        deidentified=json.loads(conv.model_dump_json()), created_at=utcnow(),
    )).inserted_primary_key[0]


def insert_result(conn, clinic_id, conversation_pk, result):
    """Store a JudgeResult; returns its pk."""
    output = result.output
    return conn.execute(judge_results.insert().values(
        clinic_id=clinic_id, conversation_pk=conversation_pk, prompt_version=result.prompt_version,
        model=result.model, outcome=result.outcome, flagged=result.flagged,
        severity=output.severity if output else None,
        result=json.loads(result.model_dump_json()), created_at=utcnow(),
    )).inserted_primary_key[0]


def write_audit(conn, clinic_id, key_prefix, action, object_id):
    conn.execute(audit_log.insert().values(clinic_id=clinic_id, api_key_prefix=key_prefix,
                                           action=action, object_id=str(object_id),
                                           created_at=utcnow()))
