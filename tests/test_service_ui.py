"""The reviewer queue web app (roadmap 2D): accounts, sessions, CSRF, tenancy, queue, feedback, export."""

import json
import re
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from conftest import RAW_EMAIL, reply
from safety_eval.clinic import Clinic
from safety_eval.schemas import CRITERIA, JudgeOutput, JudgeResult
from safety_eval.service import admin, ui
from safety_eval.service import app as app_module
from safety_eval.service.app import get_app
from safety_eval.service.db import audit_log, feedback, insert_conversation, insert_result
from safety_eval.service.settings import Settings, StartupError, require_session_secret
from safety_eval.service.ui_db import create_reviewer, disable_reviewer, hash_password, reviewers, verify_password

SECRET = "test-secret-" + "x" * 32
PW = "correct horse battery"
STATIC = Path(__file__).resolve().parents[1] / "src" / "safety_eval" / "service" / "static"


class Ui:
    """A signed-in browser for one reviewer: cookies in its own jar, CSRF header added to writes."""

    def __init__(self, svc, clinic, email, role="reviewer"):
        self.svc, self.clinic, self.email = svc, clinic, email
        self.client = TestClient(svc.app, raise_server_exceptions=False)

    def login(self, password=PW, clinic=None, email=None):
        return self.client.post("/ui/login", json={"clinic": clinic or self.clinic, "email": email or self.email,
                                                   "password": password})

    @property
    def csrf(self):
        return self.client.cookies.get("se_csrf")

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, json=None, csrf=True, **kw):
        headers = {"X-CSRF-Token": self.csrf} if csrf and self.csrf else {}
        return self.client.post(path, json=json if json is not None else {}, headers=headers, **kw)

    def feedback(self, rid, action="confirm", criterion=None, note=""):
        return self.post(f"/ui/api/results/{rid}/feedback", {"criterion": criterion, "action": action, "note": note})


def judged(conv_id, verdicts, quote=None, outcome=None):
    output = JudgeOutput.model_validate_json(reply(verdicts, quote))
    if outcome is None:
        outcome = "unsure" if "unsure" in verdicts.values() else "ok"
    return JudgeResult(conversation_id=conv_id, prompt_version="C", model="fake-model", outcome=outcome,
                       output=output, attempts=1, latency_s=0.0)


def seed(svc, hl_data, clinic, conv_id, verdicts=None, outcome=None, no_output=False):
    """Store a de-identified conversation and a judge result straight in the database."""
    data = json.loads(json.dumps(hl_data[1]))   # a Harbourline fixture; for Fernhill pass [None, fern_data]
    data["conversation_id"] = conv_id
    cl = Clinic.load(clinic)
    clean, _ = svc.deid(cl.parse_conversation(data), cl, ())
    quote = clean.turns[1].text[:30]
    if no_output:
        result = JudgeResult(conversation_id=conv_id, prompt_version="C", model="fake-model",
                             outcome="needs_review", reasons=["internal error"], attempts=0, latency_s=0.0)
    else:
        result = judged(conv_id, verdicts or {}, quote, outcome)
    with svc.engine.begin() as conn:
        pk = insert_conversation(conn, clinic, clean)
        return insert_result(conn, clinic, pk, result)


@pytest.fixture
def usvc(make_service):
    svc = make_service(session_secret=SECRET, cookie_secure=False)
    with svc.engine.begin() as conn:
        for clinic in ("harbourline", "fernhill"):
            create_reviewer(conn, clinic, f"rev@{clinic}.test", "reviewer", PW)
            create_reviewer(conn, clinic, f"boss@{clinic}.test", "admin", PW)
    return svc


@pytest.fixture
def rev(usvc):
    u = Ui(usvc, "harbourline", "rev@harbourline.test")
    assert u.login().status_code == 200
    return u


@pytest.fixture
def boss(usvc):
    u = Ui(usvc, "harbourline", "boss@harbourline.test", "admin")
    assert u.login().status_code == 200
    return u


# ------------------------------------------------------------------ passwords and accounts

def test_scrypt_hashes_are_salted_and_verify():
    a, b = hash_password(PW), hash_password(PW)
    assert a != b and a.startswith("scrypt$") and PW not in a
    assert verify_password(PW, a) and not verify_password("wrong", a)
    assert not verify_password(PW, "garbage") and not verify_password(PW, "md5$1$2$3$4$5")


def test_only_the_hash_is_stored(usvc):
    with usvc.engine.connect() as conn:
        rows = conn.execute(sa.select(reviewers)).all()
    assert len(rows) == 4 and all(PW not in r.password_hash for r in rows)


def test_admin_cli_creates_and_disables_reviewers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    admin.main(["create-clinic", "harbourline"])
    with pytest.raises(SystemExit, match="run create-clinic first"):
        admin.main(["create-reviewer", "--clinic", "fernhill", "--email", "a@b.org", "--generate"])
    capsys.readouterr()
    admin.main(["create-reviewer", "--clinic", "harbourline", "--email", "A@B.org", "--role", "admin", "--generate"])
    out = capsys.readouterr().out
    password = re.search(r"Password: (\S+)", out).group(1)
    with pytest.raises(SystemExit, match="already a reviewer"):
        admin.main(["create-reviewer", "--clinic", "harbourline", "--email", "a@b.org", "--generate"])
    answers = iter(["short", "short"])
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(SystemExit, match="at least 10"):
        admin.main(["create-reviewer", "--clinic", "harbourline", "--email", "c@d.org"])
    answers = iter([PW, PW])
    admin.main(["create-reviewer", "--clinic", "harbourline", "--email", "c@d.org"])
    answers = iter([PW, "different"])
    with pytest.raises(SystemExit, match="do not match"):
        admin.main(["create-reviewer", "--clinic", "harbourline", "--email", "e@f.org"])
    engine = admin.make_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    with engine.connect() as conn:
        row = conn.execute(sa.select(reviewers).where(reviewers.c.email == "a@b.org")).one()
    assert row.role == "admin" and verify_password(password, row.password_hash)
    admin.main(["disable-reviewer", "--clinic", "harbourline", "--email", "a@b.org"])
    with pytest.raises(SystemExit, match="no live reviewer"):
        admin.main(["disable-reviewer", "--clinic", "harbourline", "--email", "a@b.org"])


# ------------------------------------------------------------------ login and sessions

def test_login_sets_a_hardened_cookie(make_service):
    svc = make_service(session_secret=SECRET)         # default: Secure on
    with svc.engine.begin() as conn:
        create_reviewer(conn, "harbourline", "rev@x.test", "reviewer", PW)
    r = svc.client.post("/ui/login", json={"clinic": "harbourline", "email": "REV@x.test ", "password": PW})
    assert r.status_code == 200 and r.json() == {"email": "rev@x.test", "role": "reviewer", "clinic": "harbourline"}
    session = next(h for h in r.headers.get_list("set-cookie") if h.startswith("se_session="))
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/ui"):
        assert flag.lower() in session.lower()
    csrf = next(h for h in r.headers.get_list("set-cookie") if h.startswith("se_csrf="))
    assert "httponly" not in csrf.lower()


def test_login_failures_are_generic(usvc):
    with usvc.engine.begin() as conn:
        disable_reviewer(conn, "harbourline", "rev@harbourline.test")
    bodies = set()
    for clinic, email, pw in [("harbourline", "boss@harbourline.test", "wrong-password"),   # bad password
                              ("harbourline", "nobody@harbourline.test", PW),               # unknown email
                              ("nowhere", "boss@harbourline.test", PW),                     # unknown clinic
                              ("harbourline", "rev@harbourline.test", PW)]:                 # disabled
        r = Ui(usvc, clinic, email).login(pw)
        assert r.status_code == 401 and "se_session" not in r.headers.get("set-cookie", "")
        bodies.add(r.text)
    assert len(bodies) == 1


def test_login_is_rate_limited_per_ip_and_email(usvc):
    u = Ui(usvc, "harbourline", "boss@harbourline.test")
    codes = [u.login("nope").status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]
    assert Ui(usvc, "harbourline", "rev@harbourline.test").login().status_code == 200   # another email is fine


def test_api_needs_a_session(usvc):
    c = TestClient(usvc.app)
    for path in ("/ui/api/me", "/ui/api/queue", "/ui/api/results/1", "/ui/api/export/candidates.jsonl"):
        assert c.get(path).status_code == 401, path
    assert c.post("/ui/api/results/1/feedback", json={"action": "confirm"}).status_code == 401
    assert c.post("/ui/logout").status_code == 401
    # an API key is not a session
    assert c.get("/ui/api/queue", headers=usvc.headers()).status_code == 401


def test_page_redirects_to_login_without_a_session(usvc, rev):
    r = TestClient(usvc.app).get("/ui", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ui/login"
    assert rev.get("/ui").status_code == 200 and "Review queue" in rev.get("/ui").text
    assert "sign in" in TestClient(usvc.app).get("/ui/login").text.lower()


def test_tampered_forged_and_expired_cookies_are_rejected(usvc, rev):
    token = rev.client.cookies.get("se_session")
    rid = int(token.split(".")[0])
    good_rest = token.rsplit(".", 1)[1]

    def with_cookie(value):
        return TestClient(usvc.app).get("/ui/api/me", headers={"Cookie": f"se_session={value}"}).status_code

    assert with_cookie(token) == 200
    assert with_cookie(token[:-2] + ("AA" if not token.endswith("AA") else "BB")) == 401       # bad signature
    assert with_cookie(token.replace(f"{rid}.", f"{rid + 1}.", 1)) == 401                      # other reviewer id
    assert with_cookie(f"{rid}.99999999999.nonce.{good_rest}") == 401                          # edited expiry
    assert with_cookie(ui.make_session_token("another-secret-" + "y" * 30, rid)) == 401         # wrong secret
    assert with_cookie("") == 401 and with_cookie("garbage") == 401
    assert with_cookie(ui.make_session_token(SECRET, rid, expires=ui.clock() - 5)) == 401      # expired
    assert with_cookie(ui.make_session_token(SECRET, rid, expires=ui.clock() + 60)) == 200


def test_session_lasts_eight_hours(usvc, rev, monkeypatch):
    real = ui.clock
    monkeypatch.setattr(ui, "clock", lambda: real() + 7.9 * 3600)
    assert rev.get("/ui/api/me").status_code == 200
    monkeypatch.setattr(ui, "clock", lambda: real() + 8.1 * 3600)
    assert rev.get("/ui/api/me").status_code == 401


def test_disabling_a_reviewer_ends_their_session(usvc, rev):
    assert rev.get("/ui/api/me").status_code == 200
    with usvc.engine.begin() as conn:
        disable_reviewer(conn, "harbourline", "rev@harbourline.test")
    assert rev.get("/ui/api/me").status_code == 401


def test_logout_needs_csrf_and_clears_cookies(usvc, rev):
    assert rev.post("/ui/logout", csrf=False).status_code == 403
    r = rev.post("/ui/logout")
    assert r.status_code == 200 and "se_session" not in rev.client.cookies


def test_production_requires_a_session_secret(monkeypatch):
    with pytest.raises(StartupError, match="SESSION_SECRET"):
        require_session_secret(Settings())
    with pytest.raises(StartupError, match="SESSION_SECRET"):
        require_session_secret(Settings(session_secret="short"))
    require_session_secret(Settings(session_secret=SECRET))
    s = Settings.from_env({"SESSION_SECRET": SECRET, "UI_COOKIE_SECURE": "0"})
    assert s.session_secret == SECRET and s.cookie_secure is False
    assert Settings.from_env({}).cookie_secure is True


def test_ui_is_not_served_without_a_secret(make_service):
    assert make_service().client.get("/ui/login").status_code == 404


# ------------------------------------------------------------------ CSRF

def test_every_state_changing_request_needs_the_csrf_token(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "c1", {"safety": "fail"})
    url = f"/ui/api/results/{rid}/feedback"
    body = {"criterion": None, "action": "confirm", "note": ""}
    assert rev.post(url, body, csrf=False).status_code == 403
    assert rev.client.post(url, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    other = Ui(usvc, "harbourline", "boss@harbourline.test")
    other.login()
    assert rev.client.post(url, json=body, headers={"X-CSRF-Token": other.csrf}).status_code == 403   # another session's token
    assert rev.post(url, body).status_code == 201
    with usvc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(feedback)).scalar() == 1


# ------------------------------------------------------------------ clinic isolation

def test_clinic_isolation_on_every_endpoint(usvc, rev, boss, hl_data, fern_data):
    mine = seed(usvc, hl_data, "harbourline", "hl-a", {"safety": "fail"})
    seed(usvc, [None, fern_data], "fernhill", "fern-conv", {"safety": "fail"})
    with usvc.engine.connect() as conn:
        theirs = conn.execute(sa.text("select pk from judge_results where clinic_id = 'fernhill'")).scalar()
    assert theirs != mine
    # list: only mine
    items = rev.get("/ui/api/queue").json()["items"]
    assert [i["conversation_id"] for i in items] == ["hl-a"]
    # detail and feedback on the other clinic's id: 404, the same as an id that does not exist
    missing = rev.get("/ui/api/results/99999")
    assert rev.get(f"/ui/api/results/{theirs}").status_code == 404 == missing.status_code
    assert rev.get(f"/ui/api/results/{theirs}").json() == missing.json()
    assert rev.feedback(theirs, "confirm").status_code == 404
    # the other clinic's reviewer sees theirs and cannot touch mine
    fern = Ui(usvc, "fernhill", "boss@fernhill.test", "admin")
    fern.login()
    assert [i["conversation_id"] for i in fern.get("/ui/api/queue").json()["items"]] == ["fern-conv"]
    assert fern.get(f"/ui/api/results/{mine}").status_code == 404
    assert fern.feedback(mine, "overturn", "safety").status_code == 404
    # export holds only the caller's clinic, and nothing of mine leaks into theirs
    assert boss.feedback(mine, "overturn", "safety").status_code == 201
    assert fern.feedback(theirs, "overturn", "safety").status_code == 201
    mine_lines = [json.loads(x) for x in boss.get("/ui/api/export/candidates.jsonl").text.splitlines()]
    their_lines = [json.loads(x) for x in fern.get("/ui/api/export/candidates.jsonl").text.splitlines()]
    assert [x["conversation_id"] for x in mine_lines] == ["hl-a"]
    assert [x["conversation_id"] for x in their_lines] == ["fern-conv"]
    # a reviewer from clinic A cannot log in to clinic B with A's password
    assert Ui(usvc, "fernhill", "rev@harbourline.test").login().status_code == 401


# ------------------------------------------------------------------ queue

def test_queue_lists_what_needs_a_look_newest_first(usvc, rev, hl_data):
    seed(usvc, hl_data, "harbourline", "clean-ok", {})                                   # ok, not flagged: not queued
    a = seed(usvc, hl_data, "harbourline", "needs", None, outcome="needs_review", no_output=True)
    b = seed(usvc, hl_data, "harbourline", "unsure", {"scope": "unsure", "accuracy": "unsure"})
    c = seed(usvc, hl_data, "harbourline", "flagged", {"safety": "fail"})                # ok outcome but flagged
    q = rev.get("/ui/api/queue").json()
    assert q["total"] == 3 and [i["result_id"] for i in q["items"]] == [c, b, a]         # newest first
    by_id = {i["conversation_id"]: i for i in q["items"]}
    assert by_id["flagged"]["failed"] == ["safety"] and by_id["flagged"]["severity"] == "high"
    assert by_id["unsure"]["unsure"] == ["accuracy", "scope"] and by_id["unsure"]["failed"] == []
    assert by_id["needs"]["failed"] == [] and by_id["needs"]["outcome"] == "needs_review"
    assert all(set(i) == {"result_id", "conversation_id", "created_at", "outcome", "flagged", "severity",
                          "failed", "unsure", "reviewed", "feedback_count"} for i in q["items"])
    assert "turns" not in rev.get("/ui/api/queue").text and "synthetic" not in rev.get("/ui/api/queue").text
    assert [i["conversation_id"] for i in rev.get("/ui/api/queue?outcome=unsure").json()["items"]] == ["unsure"]
    assert rev.get("/ui/api/queue?outcome=ok").json()["total"] == 1
    page = rev.get("/ui/api/queue?limit=1&offset=1").json()
    assert page["total"] == 3 and [i["result_id"] for i in page["items"]] == [b]
    for bad in ("state=nope", "limit=0", "limit=1000", "offset=-1", "outcome=x"):
        assert rev.get(f"/ui/api/queue?{bad}").status_code == 422


def test_reviewed_means_a_confirm_or_overturn_exists(usvc, rev, hl_data):
    a = seed(usvc, hl_data, "harbourline", "a", {"safety": "fail"})
    b = seed(usvc, hl_data, "harbourline", "b", {"safety": "fail"})
    c = seed(usvc, hl_data, "harbourline", "c", {"safety": "fail"})
    assert rev.feedback(a, "confirm", "safety").status_code == 201
    assert rev.feedback(b, "overturn", "safety").status_code == 201
    assert rev.feedback(c, "comment", None, "looks odd").status_code == 201        # a comment alone does not review it
    ids = lambda state: {i["result_id"] for i in rev.get(f"/ui/api/queue?state={state}").json()["items"]}
    assert ids("open") == {c} and ids("reviewed") == {a, b}
    assert all(i["reviewed"] for i in rev.get("/ui/api/queue?state=reviewed").json()["items"])
    item = rev.get("/ui/api/queue").json()["items"][0]
    assert item["reviewed"] is False and item["feedback_count"] == 1
    assert rev.get(f"/ui/api/results/{c}").json()["reviewed"] is False
    assert rev.get(f"/ui/api/results/{a}").json()["reviewed"] is True


# ------------------------------------------------------------------ detail

def test_detail_has_everything_and_is_audited(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "d1", {"safety": "fail", "scope": "unsure"})
    d = rev.get(f"/ui/api/results/{rid}").json()
    assert d["conversation_id"] == "d1" and d["conversation"]["turns"] and d["conversation"]["patient_record"]
    assert [c["name"] for c in d["criteria"]] == list(CRITERIA)
    safety = d["criteria"][0]
    assert safety["verdict"] == "fail" and safety["analysis"] == "synthetic" and len(safety["evidence"]) == 1
    assert d["severity"] == "high" and d["feedback"] == []
    with usvc.engine.connect() as conn:
        rows = conn.execute(sa.select(audit_log)).all()
    assert [(r.action, r.object_id, r.clinic_id) for r in rows] == [("ui_read_result", str(rid), "harbourline")]
    assert rows[0].api_key_prefix.startswith("reviewer:")
    assert rev.get(f"/ui/api/results/{rid}").status_code == 200
    with usvc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(audit_log)).scalar() == 2
    assert rev.get("/ui/api/results/99999").status_code == 404      # a miss is not audited
    with usvc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(audit_log)).scalar() == 2


def test_detail_of_a_result_without_verdicts(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "broken", no_output=True)
    d = rev.get(f"/ui/api/results/{rid}").json()
    assert d["criteria"] == [] and d["reasons"] == ["internal error"] and d["outcome"] == "needs_review"


# ------------------------------------------------------------------ feedback

def test_feedback_is_stored_with_the_reviewer_email(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "f1", {"safety": "fail"})
    r = rev.feedback(rid, "overturn", "safety", "  not a real issue ")
    assert r.status_code == 201 and r.json()["reviewed"] is True
    with usvc.engine.connect() as conn:
        (row,) = conn.execute(sa.select(feedback)).all()
    assert (row.result_pk, row.reviewer, row.action, row.criterion, row.note, row.clinic_id) == \
        (rid, "rev@harbourline.test", "overturn", "safety", "not a real issue", "harbourline")
    shown = rev.get(f"/ui/api/results/{rid}").json()["feedback"]
    assert [(f["reviewer"], f["action"], f["criterion"]) for f in shown] == [("rev@harbourline.test", "overturn", "safety")]


def test_feedback_validation(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "f2", {"safety": "fail", "scope": "unsure"})
    bad = [
        {"criterion": "nonsense", "action": "confirm", "note": ""},          # not in CRITERIA
        {"criterion": "safety", "action": "delete", "note": ""},
        {"criterion": None, "action": "overturn", "note": ""},               # overturn needs a criterion
        {"criterion": None, "action": "comment", "note": "   "},             # comment needs text
        {"criterion": "scope", "action": "overturn", "note": ""},            # unsure cannot be flipped
        {"criterion": "safety", "action": "confirm", "note": "x" * 4001},
        {"criterion": "safety", "action": "confirm", "note": "", "reviewer": "someone else"},   # reviewer comes from the session
    ]
    for body in bad:
        assert rev.post(f"/ui/api/results/{rid}/feedback", body).status_code == 422, body
    assert rev.post("/ui/api/results/abc/feedback", {"action": "confirm"}).status_code == 422
    with usvc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(feedback)).scalar() == 0
    for body in ({"criterion": "scope", "action": "confirm", "note": ""},
                 {"criterion": None, "action": "comment", "note": "second opinion please"}):
        assert rev.post(f"/ui/api/results/{rid}/feedback", body).status_code == 201


def test_a_result_without_verdicts_can_only_be_commented_or_confirmed(usvc, rev, hl_data):
    rid = seed(usvc, hl_data, "harbourline", "f3", no_output=True)
    assert rev.feedback(rid, "overturn", "safety").status_code == 422
    assert rev.feedback(rid, "confirm").status_code == 201


# ------------------------------------------------------------------ export

def test_export_is_admin_only(usvc, rev, boss):
    assert rev.get("/ui/api/export/candidates.jsonl").status_code == 403
    r = boss.get("/ui/api/export/candidates.jsonl")
    assert r.status_code == 200 and r.text == "" and "ndjson" in r.headers["content-type"]


def test_export_content(usvc, rev, boss, hl_data):
    flip = seed(usvc, hl_data, "harbourline", "flip", {"safety": "fail", "scope": "unsure"})
    wrong = seed(usvc, hl_data, "harbourline", "false-alarm", {"accuracy": "fail"})
    seed(usvc, hl_data, "harbourline", "plain", {"safety": "fail"})                    # no overturn: not exported
    confirmed = seed(usvc, hl_data, "harbourline", "confirmed", {"safety": "fail"})
    rev.feedback(flip, "overturn", "safety", "judge too strict")
    rev.feedback(flip, "confirm", "scope")
    rev.feedback(wrong, "overturn", "accuracy")
    rev.feedback(wrong, "overturn", "grounding")                                       # a pass flipped to fail
    boss.feedback(wrong, "confirm", "accuracy")                                        # latest word on accuracy: keep the judge's fail
    rev.feedback(confirmed, "confirm")
    r = boss.get("/ui/api/export/candidates.jsonl")
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert [x["conversation_id"] for x in lines] == ["flip", "false-alarm"]
    one, two = lines
    assert one["source"] == "reviewer_feedback" and one["result_id"] == flip
    assert one["judge"]["verdicts"]["safety"] == "fail" and one["verdicts"]["safety"] == "pass"
    assert one["verdicts"]["scope"] == "unsure" and one["unsure"] is True
    assert set(one["verdicts"]) == set(CRITERIA) and one["overturned"] == ["safety"]
    assert one["severity"] == "none" and one["notes"] == ["judge too strict"]
    assert one["reviewers"] == ["rev@harbourline.test"] and one["conversation"]["turns"]
    assert two["overturned"] == ["grounding"] and two["verdicts"]["accuracy"] == "fail"
    assert two["verdicts"]["grounding"] == "fail" and two["severity"] == "high"
    assert "labeller" not in one and "pass_name" not in one          # not a HumanLabel
    with usvc.engine.connect() as conn:
        actions = [a.action for a in conn.execute(sa.select(audit_log)).all()]
    assert "ui_export_candidates" in actions


def test_export_has_no_raw_pii_and_never_touches_human_labels(usvc, boss, rev, hl_data):
    # the first Harbourline fixture carries an email address; the fake de-identifier masks it at intake
    rid = usvc.evaluate(hl_data[0]).json()["result_id"]
    with usvc.engine.begin() as conn:                       # make that result overturnable
        conn.execute(sa.text("update judge_results set result = :r, flagged = 1, outcome = 'unsure' where pk = :pk"),
                     {"r": judged("hl-001", {"safety": "fail"}, "x", "unsure").model_dump_json(), "pk": rid})
    assert rev.feedback(rid, "overturn", "safety").status_code == 201
    text = boss.get("/ui/api/export/candidates.jsonl").text
    assert text and RAW_EMAIL not in text and "[EMAIL_1]" in text
    labels = Path(__file__).resolve().parents[1] / "data" / "labels"
    before = {p: p.stat().st_mtime_ns for p in labels.glob("*")} if labels.exists() else {}
    boss.get("/ui/api/export/candidates.jsonl")
    assert before == ({p: p.stat().st_mtime_ns for p in labels.glob("*")} if labels.exists() else {})


# ------------------------------------------------------------------ static page

def page_files():
    return sorted(STATIC.glob("*.html"))


def test_static_pages_make_no_external_requests():
    assert {p.name for p in page_files()} >= {"review.html", "login.html"}
    for p in page_files():
        text = p.read_text()
        assert not re.search(r"https?:|//[a-z0-9.-]+\.[a-z]{2,}|@import|<link[^>]+href|<script[^>]+src|<iframe", text,
                             re.IGNORECASE), p.name
        assert not re.search(r"url\(\s*['\"]?(https?:|//)", text), p.name


def test_static_pages_never_put_data_into_innerhtml():
    for p in page_files():
        text = p.read_text()
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", text), p.name
        assert "textContent" in text


def test_static_pages_have_no_inline_style_or_handler_attributes():
    """The CSP allows only the page's own <style> and <script> blocks by hash, so a style="" or
    onclick="" attribute is silently blocked in the browser (found by a real-browser check)."""
    for p in page_files():
        assert not re.search(r"\sstyle\s*=|\son[a-z]+\s*=", p.read_text(), re.IGNORECASE), p.name


def test_pages_are_served_with_a_locked_down_csp(usvc, rev):
    for r in (rev.get("/ui"), TestClient(usvc.app).get("/ui/login")):
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "unsafe-inline" not in csp
        assert re.search(r"script-src 'sha256-[A-Za-z0-9+/=]+'", csp)
        assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"


# ------------------------------------------------------------------ end to end

def test_clear_a_twenty_item_queue_end_to_end(usvc, rev, boss, hl_data):
    ids = []
    for i in range(20):
        verdicts = {"safety": "fail"} if i % 2 == 0 else {"scope": "unsure"}
        outcome = "needs_review" if i % 5 == 0 else None
        ids.append(seed(usvc, hl_data, "harbourline", f"conv-{i:02d}", verdicts, outcome))
    seed(usvc, hl_data, "harbourline", "fine", {})                       # never queued
    queue = rev.get("/ui/api/queue?limit=100").json()
    assert queue["total"] == 20
    # three pages of 8, newest first, no overlap
    seen = []
    for offset in (0, 8, 16):
        seen += [x["result_id"] for x in rev.get(f"/ui/api/queue?limit=8&offset={offset}").json()["items"]]
    assert seen == sorted(ids, reverse=True)
    overturned = []
    for n, rid in enumerate(seen):
        detail = rev.get(f"/ui/api/results/{rid}").json()
        assert detail["reviewed"] is False
        failing = [c["name"] for c in detail["criteria"] if c["verdict"] == "fail"]
        if n % 3 == 1 and failing:                                       # some failing ones are false alarms
            assert rev.feedback(rid, "overturn", failing[0], "not an issue").status_code == 201
            overturned.append(rid)
        else:
            assert rev.feedback(rid, "confirm", None).status_code == 201
    assert rev.get("/ui/api/queue").json()["total"] == 0 and rev.get("/ui/api/queue").json()["items"] == []
    assert rev.get("/ui/api/queue?state=reviewed&limit=100").json()["total"] == 20
    lines = [json.loads(x) for x in boss.get("/ui/api/export/candidates.jsonl").text.splitlines()]
    assert sorted(x["result_id"] for x in lines) == sorted(overturned) and overturned
    with usvc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(feedback)).scalar() == 20
        assert conn.execute(sa.select(sa.func.count()).select_from(audit_log).where(
            audit_log.c.action == "ui_read_result")).scalar() == 20


def test_get_app_requires_the_session_secret(monkeypatch):
    monkeypatch.setattr(app_module, "production_backend", lambda settings: object())
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(StartupError, match="SESSION_SECRET"):
        get_app()
