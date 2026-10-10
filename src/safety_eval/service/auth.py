"""API keys: `sek_<random>`. Only the sha256 hash is stored, so a database leak yields no usable key."""

import hashlib
import secrets

import sqlalchemy as sa

from .db import api_keys, utcnow

KEY_PREFIX = "sek_"
SHOWN = len(KEY_PREFIX) + 8     # characters of the key kept for display


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def new_key():
    """(key, prefix, hash). The key is shown once by the admin CLI and never again."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:SHOWN], hash_key(key)


def lookup(conn, key):
    """(clinic_id, prefix) for a live key, or None for an unknown or revoked one."""
    if not key.startswith(KEY_PREFIX):
        return None
    row = conn.execute(sa.select(api_keys.c.clinic_id, api_keys.c.prefix).where(
        api_keys.c.key_hash == hash_key(key), api_keys.c.revoked_at.is_(None))).first()
    return (row.clinic_id, row.prefix) if row else None


def bearer_token(header):
    scheme, _, token = (header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def revoke(conn, prefix):
    """Revoke the live key with this prefix. Returns how many keys were revoked (0 or 1)."""
    rows = conn.execute(sa.select(api_keys.c.id).where(
        api_keys.c.prefix == prefix, api_keys.c.revoked_at.is_(None))).all()
    if len(rows) != 1:
        return 0
    conn.execute(api_keys.update().where(api_keys.c.id == rows[0].id).values(revoked_at=utcnow()))
    return 1
