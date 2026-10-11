"""Monitoring and alerts (roadmap 2F, D52): metric maths, clinic isolation, alert rules, dedupe,
the webhook payload, and the admin-only dashboard. No model is called and nothing is sent: every
sender here is a fake."""

import dataclasses
import io
import json
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import PHRASE, reply
from safety_eval.schemas import CRITERIA, JudgeOutput, JudgeResult
from safety_eval.service import monitor, monitoring
from safety_eval.service.budget import usage_cost
from safety_eval.service.db import (alert_state, conversations, feedback, judge_results, spend_ledger,
                                    utcnow)
from safety_eval.service.settings import Settings, StartupError
from safety_eval.service.ui_db import create_reviewer

import sqlalchemy as sa

SECRET = "test-secret-" + "x" * 32
PW = "correct horse battery"
TODAY = utcnow().date()
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
STATIC = Path(__file__).resolve().parents[1] / "src" / "safety_eval" / "service" / "static"


# ------------------------------------------------------------------ seeding

def at(ago, hour=12):
    return datetime.combine(TODAY - timedelta(days=ago), time(hour), tzinfo=timezone.utc)


def result_json(verdicts=None, outcome=None, reasons=(), latency=1.0, attempts=1, model="claude-sonnet-5-5",
                no_output=False):
    if no_output:
        r = JudgeResult(conversation_id="c", prompt_version="C", model=model, outcome="needs_review",
                        reasons=list(reasons), attempts=attempts, latency_s=latency)
        return r, "needs_review"
    output = JudgeOutput.model_validate_json(reply(verdicts or {}, "a quote"))
    if outcome is None:
        outcome = "unsure" if "unsure" in (verdicts or {}).values() else "ok"
    r = JudgeResult(conversation_id="c", prompt_version="C", model=model, outcome=outcome,
                    reasons=list(reasons), output=output, attempts=attempts, latency_s=latency)
    return r, outcome


def put(svc, clinic, ago=0, verdicts=None, usage=None, **kw):
    """One conversation and one judge result straight into the database, created `ago` days back.
    Returns the result pk."""
    result, _ = result_json(verdicts, **kw)
    usage = usage or {}
    with svc.engine.begin() as conn:
        cpk = conn.execute(conversations.insert().values(
            clinic_id=clinic, conversation_id="c", deidentified={"turns": []},
            created_at=at(ago))).inserted_primary_key[0]
        return conn.execute(judge_results.insert().values(
            clinic_id=clinic, conversation_pk=cpk, prompt_version="C", model=result.model,
            outcome=result.outcome, flagged=result.flagged, severity=None,
            result=json.loads(result.model_dump_json()), created_at=at(ago), **usage)).inserted_primary_key[0]


def day_of(svc, clinic, ago, n, flagged=0, needs_review=0, unsure=0):
    """`n` results on one day: the first `flagged` fail safety, then needs_review, unsure, the rest ok."""
    for i in range(n):
        if i < flagged:
            put(svc, clinic, ago, {"safety": "fail"})
        elif i < flagged + needs_review:
            put(svc, clinic, ago, no_output=True, reasons=["internal error"], attempts=0, latency=0.0)
        elif i < flagged + needs_review + unsure:
            put(svc, clinic, ago, {"scope": "unsure"})
        else:
            put(svc, clinic, ago)


def mark(svc, clinic, pk, action, criterion="safety"):
    with svc.engine.begin() as conn:
        conn.execute(feedback.insert().values(clinic_id=clinic, result_pk=pk, reviewer="r@x.test",
                                              criterion=criterion, action=action, note="", created_at=utcnow()))


def baseline(svc, clinic, rates=(0.2, 0.2), n=30, days=range(1, 29)):
    """28 days of history alternating between the given flag rates."""
    for i, ago in enumerate(days):
        day_of(svc, clinic, ago, n, flagged=round(n * rates[i % len(rates)]))


class Sender:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, payload):
        if self.fail:
            raise OSError("connection refused")
        self.calls.append((url, payload))


def cfg(**kw):
    return Settings(alert_webhook_url="https://hooks.example.test/alerts", **kw)


def codes(alerts):
    return {(a["scope"], a["code"]) for a in alerts}


# ------------------------------------------------------------------ metric maths

def test_daily_metrics_on_seeded_data(svc):
    usage = {"input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 4000, "cache_write_tokens": 500}
    pks = []
    pks.append(put(svc, "harbourline", 0, {"safety": "fail", "scope": "fail"}, latency=1.0, usage=usage))
    pks.append(put(svc, "harbourline", 0, {"safety": "fail"}, latency=2.0, usage=usage))
    pks.append(put(svc, "harbourline", 0, {"scope": "unsure"}, latency=3.0))
    pks.append(put(svc, "harbourline", 0, no_output=True, outcome="needs_review", attempts=2, latency=4.0,
                   reasons=["safety: quote not found in conversation or record", "scope: quote not found in x"]))
    pks.append(put(svc, "harbourline", 0, no_output=True, attempts=0, latency=0.0, reasons=["internal error"]))
    pks.append(put(svc, "harbourline", 0, no_output=True, attempts=1, latency=9.0,
                   reasons=["call failed: spend limit reached"]))
    pks.append(put(svc, "harbourline", 0))
    pks.append(put(svc, "harbourline", 0))
    put(svc, "harbourline", 1)                              # yesterday
    mark(svc, "harbourline", pks[0], "overturn")
    mark(svc, "harbourline", pks[1], "confirm")
    mark(svc, "harbourline", pks[6], "confirm")
    mark(svc, "harbourline", pks[7], "comment")             # a comment is not a review

    daily = monitoring.clinic_daily(svc.engine, "harbourline", 3, TODAY)
    assert [d["date"] for d in daily] == [(TODAY - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    empty, yesterday, d = daily
    assert empty["evaluated"] == 0 and empty["flag_rate"] is None and empty["latency_median_s"] is None
    assert yesterday["evaluated"] == 1
    assert d["evaluated"] == 8 and d["flagged"] == 2 and d["flag_rate"] == 0.25
    assert d["criterion_fails"] == {"safety": 2, "escalation": 0, "accuracy": 0, "scope": 1, "grounding": 0}
    assert d["criterion_flag_rate"]["safety"] == 0.25 and d["criterion_flag_rate"]["scope"] == 0.125
    assert d["unsure"] == 1 and d["unsure_rate"] == 0.125
    assert d["needs_review"] == 3 and d["needs_review_rate"] == 0.375
    assert d["review_reasons"] == {"internal_error": 1, "quote_not_found": 1, "spend_limit": 1}
    assert d["reviewed"] == 3 and d["overturned"] == 1 and d["overturn_rate"] == round(1 / 3, 6)
    # latency counts results with an attempt: 1, 2, 3, 4, 9 and the two plain ok results (1.0 each)
    assert d["latency_median_s"] == 2.0 and d["latency_p95_s"] == 7.5
    assert d["usage_recorded"] == 2 and d["input_tokens"] == 2000 and d["cache_write_tokens"] == 1000
    one = usage_cost("claude-sonnet-5-5", {"input_tokens": 1000, "output_tokens": 200,
                                           "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 500})
    assert d["est_cost_usd"] == pytest.approx(2 * one, abs=1e-5)


def test_review_reason_codes_never_carry_text():
    assert monitoring.reason_code("safety: quote not found in conversation or record") == "quote_not_found"
    assert monitoring.reason_code("scope: fail without evidence") == "missing_evidence"
    assert monitoring.reason_code("call failed: spend limit reached") == "spend_limit"
    assert monitoring.reason_code("call failed: timeout") == "call_failed"
    assert monitoring.reason_code(f"malformed output: {PHRASE}") == "malformed_output"
    assert monitoring.reason_code("inconsistent output: x") == "inconsistent_output"
    assert monitoring.reason_code("internal error") == "internal_error"
    assert monitoring.reason_code(PHRASE) == "other"
    assert set(monitoring.REASON_CODES) >= {monitoring.reason_code(r) for r in ("a", "internal error")}


def test_needs_review_without_reasons_counts_as_other(svc):
    put(svc, "harbourline", 0, no_output=True, reasons=[], attempts=1)
    d = monitoring.clinic_daily(svc.engine, "harbourline", 1, TODAY)[0]
    assert d["review_reasons"] == {"other": 1}


def test_percentile_matches_postgres_percentile_cont():
    assert monitoring.percentile([], 0.5) is None
    assert monitoring.percentile([5], 0.95) == 5
    assert monitoring.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert monitoring.percentile([1, 2, 3, 4, 5], 0.95) == pytest.approx(4.8)


def test_results_outside_the_window_and_before_midnight_are_bucketed_by_utc_day(svc):
    put(svc, "harbourline", 40)
    with svc.engine.begin() as conn:     # 23:59 UTC yesterday belongs to yesterday
        pk = put(svc, "harbourline", 1)
        conn.execute(judge_results.update().where(judge_results.c.pk == pk).values(
            created_at=at(1, 23).replace(minute=59)))
    daily = monitoring.clinic_daily(svc.engine, "harbourline", 2, TODAY)
    assert [d["evaluated"] for d in daily] == [1, 0]


def test_clinics_never_see_each_others_numbers(svc):
    a = put(svc, "harbourline", 0, {"safety": "fail"})
    day_of(svc, "fernhill", 0, 5, flagged=5)
    mark(svc, "fernhill", a, "overturn")           # a feedback row that names the other clinic: not counted
    h = monitoring.clinic_daily(svc.engine, "harbourline", 1, TODAY)[0]
    f = monitoring.clinic_daily(svc.engine, "fernhill", 1, TODAY)[0]
    assert (h["evaluated"], h["flagged"], h["reviewed"]) == (1, 1, 0)
    assert (f["evaluated"], f["flagged"], f["reviewed"]) == (5, 5, 0)


# ------------------------------------------------------------------ alert rules

def test_a_spike_fires(svc):
    baseline(svc, "harbourline", (0.2, 0.2667))                       # 6 or 8 of 30
    day_of(svc, "harbourline", 0, 30, flagged=15)                     # 50%
    alerts = monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)
    by = {a["code"]: a for a in alerts}
    assert "flag_rate_shift" in by and by["flag_rate_shift"]["direction"] == "high"
    assert by["flag_rate_shift"]["value"] == 0.5 and by["flag_rate_shift"]["n"] == 30
    assert "criterion_flag_rate_shift:safety" in by
    assert not {"unsure_rate_shift", "needs_review_rate_shift"} & set(by)


def test_a_drop_also_fires(svc):
    baseline(svc, "harbourline", (0.4, 0.4667))
    day_of(svc, "harbourline", 0, 30, flagged=0)
    by = {a["code"]: a for a in monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)}
    assert by["flag_rate_shift"]["direction"] == "low"


def test_low_volume_does_not_fire(svc):
    baseline(svc, "harbourline", (0.2, 0.2667))
    day_of(svc, "harbourline", 0, 19, flagged=19)                     # every one flagged, but only 19
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []
    day_of(svc, "harbourline", 0, 1, flagged=1)                       # 20th result: now it counts
    assert "flag_rate_shift" in {a["code"] for a in monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)}


def test_the_absolute_floor_stops_noise_on_a_flat_history(svc):
    baseline(svc, "harbourline", (0.2, 0.2))                          # sd 0: any change is "infinite" sigmas
    day_of(svc, "harbourline", 0, 30, flagged=7)                      # 23.3% vs 20%: 3 points
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []
    tight = Settings(alert_min_shift=0.02)
    assert "flag_rate_shift" in {a["code"] for a in monitoring.clinic_alerts(svc.engine, "harbourline", tight, TODAY)}


def test_a_noisy_history_widens_the_band(svc):
    baseline(svc, "harbourline", (0.05, 0.45))                        # mean 25%, sd about 20 points
    day_of(svc, "harbourline", 0, 30, flagged=15)                     # 50%: 25 points, but only 1.2 sd
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []


def test_too_little_history_does_not_fire(svc):
    baseline(svc, "harbourline", (0.2, 0.2667), days=range(1, 6))     # five days
    day_of(svc, "harbourline", 0, 30, flagged=30)
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []


def test_unsure_and_needs_review_spikes(svc):
    for i, ago in enumerate(range(1, 29)):
        day_of(svc, "harbourline", ago, 30, unsure=3 + i % 2, needs_review=1 + i % 2)
    day_of(svc, "harbourline", 0, 30, unsure=12, needs_review=2)
    codes_ = {a["code"] for a in monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)}
    assert "unsure_rate_shift" in codes_ and "needs_review_rate_high" not in codes_


def test_needs_review_ceiling(svc):
    day_of(svc, "harbourline", 0, 20, needs_review=3)                 # 15% exactly: not above
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []
    day_of(svc, "harbourline", 0, 1, needs_review=1)                  # 4 of 21
    [a] = monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)
    assert a["code"] == "needs_review_rate_high" and a["ceiling"] == 0.15 and a["value"] == round(4 / 21, 4)
    # a stricter ceiling from settings
    day_of(svc, "fernhill", 0, 20, needs_review=1)
    assert monitoring.clinic_alerts(svc.engine, "fernhill", Settings(), TODAY) == []
    strict = Settings(alert_needs_review_ceiling=0.04)
    assert [a["code"] for a in monitoring.clinic_alerts(svc.engine, "fernhill", strict, TODAY)] == ["needs_review_rate_high"]


def test_overturn_ceiling_needs_enough_reviewed_results(svc):
    pks = [put(svc, "harbourline", i % 3) for i in range(10)]
    for i, pk in enumerate(pks[:9]):
        mark(svc, "harbourline", pk, "overturn" if i < 5 else "confirm")
    assert monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY) == []     # 9 reviewed
    mark(svc, "harbourline", pks[9], "confirm")                                             # 10 reviewed, 5 overturned
    [a] = monitoring.clinic_alerts(svc.engine, "harbourline", Settings(), TODAY)
    assert a["code"] == "overturn_rate_high" and a["value"] == 0.5 and a["n"] == 10
    assert monitoring.clinic_alerts(svc.engine, "fernhill", Settings(), TODAY) == []         # not theirs


def test_spend_alert_is_global_and_uses_the_limit(svc):
    s = Settings(spend_limit_usd=10.0)
    with svc.engine.begin() as conn:
        conn.execute(spend_ledger.insert().values(created_at=utcnow(), kind="call", model="m", usd=7.9, settled=True))
    assert monitoring.spend_alerts(svc.engine, s) == []
    with svc.engine.begin() as conn:
        conn.execute(spend_ledger.insert().values(created_at=utcnow(), kind="call", model="m", usd=0.1, settled=False))
    [a] = monitoring.compute_alerts(svc.engine, s, TODAY)
    assert (a["scope"], a["code"], a["spent_usd"], a["limit_usd"], a["value"]) == ("global", "spend_near_limit", 8.0, 10.0, 0.8)
    assert monitoring.spend_alerts(svc.engine, Settings()) == []                            # no limit set
    assert monitoring.spend_alerts(svc.engine, Settings(spend_limit_usd=10.0, alert_spend_fraction=0.9)) == []


def test_alert_settings_come_from_the_environment():
    s = Settings.from_env({"ALERT_WEBHOOK_URL": " https://h.example/x ", "ALERT_SIGMA": "3", "ALERT_MIN_VOLUME": "50",
                           "ALERT_MIN_SHIFT": "0.2", "ALERT_NEEDS_REVIEW_CEILING": "0.1",
                           "ALERT_OVERTURN_CEILING": "0.4", "ALERT_MIN_REVIEWED": "5",
                           "ALERT_SPEND_FRACTION": "0.5", "ALERT_COOLDOWN_HOURS": "6"})
    assert (s.alert_webhook_url, s.alert_sigma, s.alert_min_volume, s.alert_min_shift) == ("https://h.example/x", 3.0, 50, 0.2)
    assert (s.alert_needs_review_ceiling, s.alert_overturn_ceiling, s.alert_min_reviewed) == (0.1, 0.4, 5)
    assert (s.alert_spend_fraction, s.alert_cooldown_hours) == (0.5, 6.0)
    d = Settings.from_env({})
    assert d.alert_webhook_url == "" and d.alert_sigma == 2.0 and d.alert_min_volume == 20
    assert d.alert_needs_review_ceiling == 0.15 and d.alert_spend_fraction == 0.8
    with pytest.raises(StartupError):
        Settings.from_env({"ALERT_SIGMA": "lots"})


# ------------------------------------------------------------------ notification

def spiking(svc):
    baseline(svc, "harbourline", (0.2, 0.2667))
    day_of(svc, "harbourline", 0, 30, flagged=15)


def test_nothing_is_sent_without_notify_or_a_url(svc, capsys):
    spiking(svc)
    sender = Sender()
    url = svc.engine.url.render_as_string(hide_password=False)
    alerts = monitor.main(["check"], settings=Settings(database_url=url), sender=sender)
    assert alerts and not sender.calls
    assert "flag_rate_shift" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="ALERT_WEBHOOK_URL"):
        monitor.main(["check", "--notify"], settings=Settings(database_url=url), sender=sender)
    with pytest.raises(ValueError):
        monitoring.notify(svc.engine, Settings(), alerts, sender)
    assert not sender.calls


def test_the_same_alert_is_not_sent_every_run(svc):
    spiking(svc)
    s, sender = cfg(), Sender()
    alerts = monitoring.compute_alerts(svc.engine, s, TODAY)
    sent = monitoring.notify(svc.engine, s, alerts, sender)
    assert sent and len(sender.calls) == 1
    assert monitoring.notify(svc.engine, s, alerts, sender) == [] and len(sender.calls) == 1   # deduped
    later = utcnow() + timedelta(hours=25)
    assert monitoring.notify(svc.engine, s, alerts, sender, now=later) and len(sender.calls) == 2   # reminder after cooldown


def test_a_cleared_alert_fires_again_when_it_returns(svc):
    spiking(svc)
    s, sender = cfg(), Sender()
    alerts = monitoring.compute_alerts(svc.engine, s, TODAY)
    monitoring.notify(svc.engine, s, alerts, sender)
    assert monitoring.notify(svc.engine, s, [], sender) == []             # all clear: state forgotten
    with svc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(alert_state)).scalar() == 0
    assert monitoring.notify(svc.engine, s, alerts, sender)               # back again: sent at once
    assert len(sender.calls) == 2


def test_only_new_alerts_are_sent(svc):
    spiking(svc)
    s, sender = cfg(), Sender()
    alerts = monitoring.compute_alerts(svc.engine, s, TODAY)
    first, rest = alerts[:1], alerts
    monitoring.notify(svc.engine, s, first, sender)
    sent = monitoring.notify(svc.engine, s, rest, sender)
    assert codes(sent) == codes(rest) - codes(first)


def test_a_failed_send_records_nothing_and_retries(svc):
    spiking(svc)
    s = cfg()
    alerts = monitoring.compute_alerts(svc.engine, s, TODAY)
    with pytest.raises(OSError):
        monitoring.notify(svc.engine, s, alerts, Sender(fail=True))
    with svc.engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(alert_state)).scalar() == 0
    good = Sender()
    assert monitoring.notify(svc.engine, s, alerts, good) and len(good.calls) == 1


def test_cli_notify_reports_failure_without_the_url(svc, capsys):
    spiking(svc)
    url = svc.engine.url.render_as_string(hide_password=False)
    s = cfg(database_url=url)
    with pytest.raises(SystemExit) as e:
        monitor.main(["check", "--notify"], settings=s, sender=Sender(fail=True))
    assert "OSError" in str(e.value) and "hooks.example" not in str(e.value)
    good = Sender()
    monitor.main(["check", "--notify"], settings=s, sender=good)
    assert len(good.calls) == 1 and good.calls[0][0] == s.alert_webhook_url
    assert "sent" in capsys.readouterr().out


def test_webhook_payload_has_codes_and_numbers_but_no_text(svc):
    # a distinctive phrase everywhere text can live: the stored conversation, evidence, analysis,
    # judge reasons and reviewer notes
    spiking(svc)
    with svc.engine.begin() as conn:
        conn.execute(conversations.update().values(deidentified={"turns": [{"text": PHRASE}]}))
        for r in conn.execute(sa.select(judge_results.c.pk, judge_results.c.result)).all():
            data = json.loads(json.dumps(r.result))
            data["reasons"] = [f"call failed: {PHRASE}"]
            for j in ((data.get("output") or {}).get("criteria") or {}).values():
                j["analysis"], j["evidence"] = PHRASE, [PHRASE]
            conn.execute(judge_results.update().where(judge_results.c.pk == r.pk).values(result=data))
        conn.execute(feedback.insert().values(clinic_id="harbourline", result_pk=1, reviewer=PHRASE,
                                              criterion="safety", action="comment", note=PHRASE,
                                              created_at=utcnow()))
    s, sender = cfg(spend_limit_usd=1.0), Sender()
    with svc.engine.begin() as conn:
        conn.execute(spend_ledger.insert().values(created_at=utcnow(), kind="call", model="m", usd=0.9, settled=True))
    alerts = monitoring.compute_alerts(svc.engine, s, TODAY)
    assert {"spend_near_limit", "flag_rate_shift"} <= {a["code"] for a in alerts}
    monitoring.notify(svc.engine, s, alerts, sender)
    [(url, payload)] = sender.calls
    wire = json.dumps(payload)
    assert PHRASE not in wire and "wombat" not in wire
    allowed = {"scope", "code", "metric", "direction", "date", "value", "baseline_mean", "baseline_sd", "sigma",
               "ceiling", "threshold", "n", "window_days", "spent_usd", "limit_usd"}
    assert payload["alerts"] and all(set(a) <= allowed for a in payload["alerts"])
    assert all(isinstance(v, (int, float)) or k in {"scope", "code", "metric", "direction", "date"}
               for a in payload["alerts"] for k, v in a.items())
    out = io.StringIO()
    monitor.main(["check", "--json"], settings=Settings(database_url=svc.engine.url.render_as_string(hide_password=False)), out=out)
    assert PHRASE not in out.getvalue()


def test_the_real_sender_refuses_other_schemes():
    with pytest.raises(ValueError):
        monitoring.post_json("file:///etc/passwd", {})
    with pytest.raises(ValueError):
        monitoring.post_json("ftp://example.test/x", {})


def test_report_includes_cost_for_the_operator(svc, capsys):
    put(svc, "harbourline", 0, usage={"input_tokens": 1000, "output_tokens": 1000,
                                      "cache_read_tokens": 0, "cache_write_tokens": 0})
    url = svc.engine.url.render_as_string(hide_password=False)
    monitor.main(["report", "--clinic", "harbourline", "--days", "2"], settings=Settings(database_url=url))
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 2 and rows[-1]["est_cost_usd"] == pytest.approx(0.012)


# ------------------------------------------------------------------ dashboard

@pytest.fixture
def usvc(make_service):
    svc = make_service(session_secret=SECRET, cookie_secure=False)
    with svc.engine.begin() as conn:
        for clinic in ("harbourline", "fernhill"):
            create_reviewer(conn, clinic, f"rev@{clinic}.test", "reviewer", PW)
            create_reviewer(conn, clinic, f"boss@{clinic}.test", "admin", PW)
    return svc


def signed_in(svc, clinic, email):
    c = TestClient(svc.app, raise_server_exceptions=False)
    assert c.post("/ui/login", json={"clinic": clinic, "email": email, "password": PW}).status_code == 200
    return c


def test_dashboard_page_is_admin_only(usvc):
    anon = TestClient(usvc.app, follow_redirects=False)
    r = anon.get("/ui/dashboard")
    assert r.status_code == 303 and r.headers["location"] == "/ui/login"
    assert anon.get("/ui/api/metrics").status_code == 401
    rev = signed_in(usvc, "harbourline", "rev@harbourline.test")
    assert rev.get("/ui/dashboard").status_code == 403
    assert rev.get("/ui/api/metrics").status_code == 403
    boss = signed_in(usvc, "harbourline", "boss@harbourline.test")
    page = boss.get("/ui/dashboard")
    assert page.status_code == 200 and "Dashboard" in page.text
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "unsafe-inline" not in csp
    assert re.search(r"script-src 'sha256-[A-Za-z0-9+/=]+'", csp) and re.search(r"style-src 'sha256-", csp)
    assert page.headers["cache-control"] == "no-store" and page.headers["x-frame-options"] == "DENY"


def test_metrics_endpoint_shows_only_the_clinics_own_numbers(usvc):
    day_of(usvc, "harbourline", 0, 4, flagged=1)
    day_of(usvc, "fernhill", 0, 9, flagged=9)
    boss = signed_in(usvc, "harbourline", "boss@harbourline.test")
    data = boss.get("/ui/api/metrics?days=7").json()
    assert data["clinic"] == "harbourline" and data["days"] == 7 and len(data["daily"]) == 7
    assert data["daily"][-1]["evaluated"] == 4 and data["daily"][-1]["flagged"] == 1
    assert data["alerts"] == [] and data["thresholds"]["sigma"] == 2.0
    f = signed_in(usvc, "fernhill", "boss@fernhill.test").get("/ui/api/metrics?days=1").json()
    assert f["daily"][-1]["evaluated"] == 9
    assert boss.get("/ui/api/metrics?days=0").status_code == 422
    assert boss.get("/ui/api/metrics?days=91").status_code == 422


def test_spend_and_cost_never_reach_the_clinic(make_service):
    svc = make_service(session_secret=SECRET, cookie_secure=False, spend_limit_usd=1.0)
    with svc.engine.begin() as conn:
        create_reviewer(conn, "harbourline", "boss@harbourline.test", "admin", PW)
        conn.execute(spend_ledger.insert().values(created_at=utcnow(), kind="call", model="m", usd=0.95, settled=True))
    put(svc, "harbourline", 0, usage={"input_tokens": 5000, "output_tokens": 100,
                                      "cache_read_tokens": 0, "cache_write_tokens": 0})
    assert [a["code"] for a in monitoring.compute_alerts(svc.engine, Settings(spend_limit_usd=1.0), TODAY)] \
        == ["spend_near_limit"]
    boss = signed_in(svc, "harbourline", "boss@harbourline.test")
    text = boss.get("/ui/api/metrics").text.lower()
    assert "spend" not in text and "cost" not in text and "limit_usd" not in text
    page = boss.get("/ui/dashboard").text.lower()
    assert "est_cost" not in page and "limit_usd" not in page and "usd" not in page
    assert "spend" not in page.replace('spend_limit: "spend limit"', "")    # bar the needs-review reason label


def test_review_queue_links_to_the_dashboard_for_admins_only():
    html = (STATIC / "review.html").read_text()
    assert 'id="dashLink"' in html and 'href="/ui/dashboard" hidden' in html
    assert '$("dashLink").hidden = me.role !== "admin"' in html


def test_dashboard_page_follows_the_ui_rules():
    html = (STATIC / "dashboard.html").read_text()
    assert not re.search(r"\sstyle\s*=|\son[a-z]+\s*=", html, re.IGNORECASE)
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function", html)
    assert not re.search(r"https?:|//[a-z0-9.-]+\.[a-z]{2,}|@import|<link[^>]+href|<script[^>]+src", html, re.IGNORECASE)
    assert "textContent" in html and 'name="viewport"' in html and "prefers-color-scheme: dark" in html
    assert "(max-width: 600px)" in html and "overflow-x: auto" in html
    assert html.count("<script>") == 1 and html.count("<style>") == 1


# ------------------------------------------------------------------ SQL files

def test_monitoring_sql_is_read_only_and_documented():
    files = sorted((SQL_DIR / "monitoring").glob("*.sql"))
    assert [f.name for f in files] == ["01_daily_metrics.sql", "02_review_reasons.sql", "03_estimated_cost.sql"]
    for f in files:
        text = f.read_text()
        code = re.sub(r"--[^\n]*", "", text)
        assert text.lstrip().startswith("--") and "read-only" in text.lower(), f.name
        assert re.findall(r"\bcreate\s+(?:or\s+replace\s+)?(\w+)", code, re.IGNORECASE) == ["view"], f.name
        assert not re.search(r"\b(insert|update|delete|drop|alter|truncate|grant)\b", code, re.IGNORECASE), f.name
        assert "deidentified" not in code and "note" not in code.lower().split(), f.name     # never selects text


def test_monitoring_sql_covers_every_metric():
    daily = (SQL_DIR / "monitoring" / "01_daily_metrics.sql").read_text()
    for c in CRITERIA:
        assert f"{c}_fails" in daily and f"'{{output,criteria,{c},verdict}}'" in daily
    for col in ("flag_rate", "unsure_rate", "needs_review_rate", "overturn_rate", "latency_median_s",
                "latency_p95_s", "input_tokens", "cache_write_tokens"):
        assert col in daily
    reasons = (SQL_DIR / "monitoring" / "02_review_reasons.sql").read_text()
    for code in monitoring.REASON_CODES:
        assert f"'{code}'" in reasons
    cost = (SQL_DIR / "monitoring" / "03_estimated_cost.sql").read_text()
    from safety_eval.service.budget import PRICES
    for model, (i, o, r) in PRICES.items():
        assert re.search(rf"'{model}',\s*{i:.2f},\s*{o:.2f},\s*{r:.2f}", cost), model     # kept in step with budget.PRICES


def test_alert_state_migration_exists():
    text = (SQL_DIR / "005_monitoring.sql").read_text()
    assert "create table alert_state" in text and "primary key (scope, code)" in text
