"""Reviewer accounts for the review queue (roadmap 2D). Same SQLAlchemy `metadata` as db.py, so
SQLite gets the table automatically; Postgres gets it from sql/003_reviewers.sql.

Passwords are stored as scrypt hashes (hashlib, no new dependency): `scrypt$n$r$p$salt$hash`.
"""

import base64
import hashlib
import hmac
import secrets

import sqlalchemy as sa

from .db import Time, metadata, utcnow

ROLES = ("admin", "reviewer")
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

reviewers = sa.Table(
    "reviewers", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("clinic_id", sa.Text, sa.ForeignKey("clinics.id"), nullable=False),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("created_at", Time, nullable=False),
    sa.Column("disabled_at", Time),
    sa.UniqueConstraint("clinic_id", "email"),
    sa.CheckConstraint("role in ('admin', 'reviewer')", name="reviewers_role_check"),
)


def _b64(raw):
    return base64.b64encode(raw).decode()


def _scrypt(password, salt, n, r, p):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    """Constant-time check. Any malformed stored value is simply a failed check."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        got = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# Verified against when the email is unknown, so response time does not reveal which emails exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def normalise_email(email):
    return email.strip().lower()


def create_reviewer(conn, clinic_id, email, role, password):
    email = normalise_email(email)
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if "@" not in email or len(email) > 200:
        raise ValueError("not a valid email address")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    return conn.execute(reviewers.insert().values(
        clinic_id=clinic_id, email=email, role=role, password_hash=hash_password(password),
        created_at=utcnow())).inserted_primary_key[0]


def disable_reviewer(conn, clinic_id, email):
    """Returns how many live reviewers were disabled (0 or 1)."""
    return conn.execute(reviewers.update().where(
        reviewers.c.clinic_id == clinic_id, reviewers.c.email == normalise_email(email),
        reviewers.c.disabled_at.is_(None)).values(disabled_at=utcnow())).rowcount
