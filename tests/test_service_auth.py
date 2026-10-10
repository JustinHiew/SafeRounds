"""API keys and the admin CLI (roadmap 2C)."""

import hashlib

import pytest
import sqlalchemy as sa

from safety_eval.service import admin
from safety_eval.service.auth import revoke
from safety_eval.service.db import api_keys

ROUTES = [("post", "/v1/evaluate"), ("post", "/v1/batches"), ("get", "/v1/batches/x"),
          ("post", "/v1/feedback"), ("get", "/v1/clinics/me/stats"), ("get", "/v1/conversations/1")]


def test_healthz_needs_no_key(svc):
    assert svc.client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_rejects_missing_and_bad_keys(svc, method, path):
    call = getattr(svc.client, method)
    assert call(path).status_code == 401
    for header in ("Bearer sek_wrong", "Bearer nonsense", "Basic abc", svc.keys["harbourline"]):
        assert call(path, headers={"Authorization": header}).status_code == 401


def test_revoked_key_is_rejected(svc):
    prefix = svc.keys["harbourline"][:12]
    assert svc.get("/v1/clinics/me/stats").status_code == 200
    with svc.engine.begin() as conn:
        assert revoke(conn, prefix) == 1
        assert revoke(conn, prefix) == 0           # already revoked
    assert svc.get("/v1/clinics/me/stats").status_code == 401
    assert svc.get("/v1/clinics/me/stats", clinic="fernhill").status_code == 200


def test_only_the_hash_of_a_key_is_stored(svc):
    key = svc.keys["harbourline"]
    with svc.engine.connect() as conn:
        row = conn.execute(sa.select(api_keys).where(api_keys.c.clinic_id == "harbourline")).one()
    assert key.startswith("sek_") and row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert key not in row and row.prefix == key[:12]


def test_admin_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    admin.main(["create-clinic", "harbourline"])
    with pytest.raises(SystemExit, match="already exists"):
        admin.main(["create-clinic", "harbourline"])
    with pytest.raises(SystemExit, match="no clinic"):
        admin.main(["create-clinic", "not-a-clinic"])
    with pytest.raises(SystemExit, match="run create-clinic first"):
        admin.main(["create-key", "--clinic", "fernhill"])
    capsys.readouterr()
    admin.main(["create-key", "--clinic", "harbourline"])
    key = capsys.readouterr().out.split()[0]
    assert key.startswith("sek_")
    admin.main(["revoke-key", key[:12]])
    with pytest.raises(SystemExit, match="no live key"):
        admin.main(["revoke-key", key[:12]])
