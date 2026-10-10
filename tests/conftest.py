"""Shared fixtures for the service tests. No real model is ever called: every backend is a FakeBackend."""

import json
import itertools
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from safety_eval.labelling import load_conversations
from safety_eval.llm import FakeBackend
from safety_eval.schemas import CRITERIA
from safety_eval.service import admin
from safety_eval.service.app import create_app
from safety_eval.service.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL = re.compile(r"[\w.]+@[\w.]+\.\w+")
PHRASE = "zq-wombat-7731"            # distinctive synthetic text that must never reach a log
RAW_EMAIL = "jo.citizen@example.com"


class FakeDeidentifier:
    """Stands in for safety_eval.deid (Phase 2B): emails become [EMAIL_n], known names [NAME_n]."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def __call__(self, conv, clinic, known_names=()):
        self.calls.append((conv.conversation_id, tuple(known_names)))
        if self.fail:
            raise RuntimeError(f"de-identifier broke on: {PHRASE}")
        mapping = {}

        def scrub(text):
            for name in (n for n in known_names if n in text):
                token = mapping.setdefault(name, f"[NAME_{len(mapping) + 1}]")
                text = text.replace(name, token)
            return EMAIL.sub(lambda m: mapping.setdefault(m.group(), f"[EMAIL_{len(mapping) + 1}]"), text)

        data = conv.model_dump()
        for turn in data["turns"]:
            turn["text"] = scrub(turn["text"])
        return type(conv).model_validate(data), mapping


def reply(verdicts=None, quote=None):
    """A judge reply. All pass by default (a pass needs no evidence); `verdicts` overrides per criterion."""
    verdicts = verdicts or {}
    crits = {c: {"evidence": [quote] if verdicts.get(c, "pass") != "pass" else [],
                 "analysis": "synthetic", "verdict": verdicts.get(c, "pass")} for c in CRITERIA}
    failed = "fail" in verdicts.values()
    return json.dumps({"criteria": crits, "overall_flag": failed, "severity": "high" if failed else "none"})


PASS_REPLY = reply()


@pytest.fixture(scope="session")
def hl_data():
    """Harbourline fixture conversations, with an email and a distinctive phrase added to the first."""
    data = json.loads((FIXTURES / "clinics" / "harbourline_conversations.json").read_text())
    data[0]["turns"][0]["text"] += f" You can email me at {RAW_EMAIL}. {PHRASE}."
    return data


@pytest.fixture(scope="session")
def fern_data():
    return json.loads(load_conversations()[0].model_dump_json())


@dataclass
class Service:
    client: TestClient
    engine: object
    keys: dict
    backend: object
    deid: FakeDeidentifier
    app: object

    def headers(self, clinic="harbourline"):
        return {"Authorization": f"Bearer {self.keys[clinic]}"}

    def post(self, path, clinic="harbourline", **kw):
        return self.client.post(path, headers=self.headers(clinic), **kw)

    def get(self, path, clinic="harbourline", **kw):
        return self.client.get(path, headers=self.headers(clinic), **kw)

    def evaluate(self, conv, clinic="harbourline", **extra):
        return self.post("/v1/evaluate", clinic, json={"conversation": conv, **extra})


@pytest.fixture
def make_service(tmp_path):
    counter = itertools.count()

    def make(backend=None, deid=None, **settings):
        settings = Settings(database_url=f"sqlite:///{tmp_path / f'service{next(counter)}.db'}", **settings)
        backend = backend or FakeBackend(PASS_REPLY)
        deid = deid or FakeDeidentifier()
        app = create_app(settings, backend, deid)
        keys = {}
        for cid in ("harbourline", "fernhill"):
            admin.create_clinic(app.state.engine, cid)
            keys[cid] = admin.create_key(app.state.engine, cid)
        return Service(TestClient(app, raise_server_exceptions=False), app.state.engine, keys,
                       backend, deid, app)
    return make


@pytest.fixture
def svc(make_service):
    return make_service()
