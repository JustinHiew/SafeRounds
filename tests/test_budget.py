"""The spend limit (D49): costs, reservations, refusing work, and batches sent in budget-sized chunks.
Every Anthropic client is a fake; no real call is ever made."""

from types import SimpleNamespace

import pytest

from conftest import reply
from test_caching_and_batches import FakeAnthropic, api_backend, batch_status, message, results_of, submit
from safety_eval.llm import ModelCallError
from safety_eval.service import admin
from safety_eval.service.budget import (PRICES, Budget, BudgetedBackend, BudgetExceeded, usage_cost,
                                        worst_case)
from safety_eval.service.db import make_engine
from safety_eval.service.settings import Settings, StartupError, production_backend
from safety_eval.service.worker import process_batch_api, process_pending


@pytest.fixture
def engine(tmp_path):
    return make_engine(f"sqlite:///{tmp_path / 'b.db'}")


def test_usage_cost_uses_published_rates():
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
         "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    # Sonnet 5.5: $2 in, $10 out, cache write 1.25x in, cache read $0.20
    assert usage_cost("claude-sonnet-5-5", u) == pytest.approx(2 + 10 + 2.5 + 0.2)
    assert usage_cost("claude-sonnet-5-5", u, batch=True) == pytest.approx((2 + 10 + 2.5 + 0.2) / 2)
    # an unknown served model (a refusal fallback) is costed at the dearest known rates
    assert usage_cost("claude-mystery", {"output_tokens": 1_000_000}) == max(p[1] for p in PRICES.values())


def test_worst_case_is_conservative():
    # 3,000 characters is about 750 real tokens; it is costed as 1,000, plus all 16,000 output tokens
    assert worst_case("claude-sonnet-5-5", 3000, 16000) == pytest.approx((1000 * 2 + 16000 * 10) / 1e6)


def test_reserve_settle_and_refuse(engine):
    budget = Budget(engine, 1.00)
    a = budget.reserve(0.60, "call", "claude-sonnet-5-5")
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.50, "call", "claude-sonnet-5-5")       # 0.60 reserved + 0.50 > 1.00
    budget.settle(a, 0.02)
    budget.reserve(0.50, "call", "claude-sonnet-5-5")           # fits once settled at the real cost
    s = budget.summary()
    assert s["settled_usd"] == 0.02 and s["reserved_usd"] == 0.5 and s["remaining_usd"] == pytest.approx(0.48)


def test_budget_needs_a_positive_limit(engine):
    for bad in (None, 0, -5):
        with pytest.raises(ValueError):
            Budget(engine, bad)


def _wrapped(engine, client, limit=5.0):
    return BudgetedBackend(api_backend(client), Budget(engine, limit))


def test_each_call_is_settled_at_its_real_cost(engine):
    backend = _wrapped(engine, FakeAnthropic(live=lambda **kw: message(reply(), usage=(1000, 500, 0, 0))))
    backend.complete("sys", "prompt")
    assert backend.budget.summary()["settled_usd"] == pytest.approx((1000 * 2 + 500 * 10) / 1e6, abs=1e-4)
    assert backend.budget.summary()["open_reservations"] == 0


def test_a_refusal_is_charged_but_an_http_error_is_not(engine):
    refused = _wrapped(engine, FakeAnthropic(live=lambda **kw: message(None, stop="refusal",
                                                                         usage=(1000, 0, 0, 0))))
    with pytest.raises(ModelCallError):
        refused.complete("sys", "prompt")
    assert refused.budget.summary()["settled_usd"] == pytest.approx(0.002)

    class HTTPError(Exception):
        status_code = 500

    def boom(**kw):
        raise HTTPError("server error")
    failed = _wrapped(engine, FakeAnthropic(live=boom))
    with pytest.raises(ModelCallError):
        failed.complete("sys", "prompt")
    assert failed.budget.summary()["open_reservations"] == 0


def test_a_timeout_keeps_its_reservation(engine):
    def timeout(**kw):
        raise TimeoutError("no response")
    backend = _wrapped(engine, FakeAnthropic(live=timeout))
    with pytest.raises(ModelCallError):
        backend.complete("sys", "prompt")
    assert backend.budget.summary()["open_reservations"] == 1     # unknown spend stays counted
    assert backend.budget.release_open() == 1


def test_no_call_is_sent_once_the_limit_could_be_passed(engine):
    client = FakeAnthropic()
    backend = _wrapped(engine, client, limit=0.10)     # less than one call's worst case
    with pytest.raises(BudgetExceeded):
        backend.complete("sys", "prompt")
    assert client.calls == client.beta_calls == []


def test_warning_at_80_percent(engine, caplog):
    budget = Budget(engine, 1.00)
    budget.reserve(0.5, "call", "m")
    assert "spend is at" not in caplog.text
    budget.reserve(0.35, "call", "m")
    assert "spend is at 85%" in caplog.text


# ------------------------------------------------------------------ the service

def test_evaluate_refuses_with_503_when_the_limit_is_reached(make_service, hl_data):
    client = FakeAnthropic()
    svc = make_service(backend=api_backend(client), spend_limit_usd=0.10)
    r = svc.evaluate(hl_data[0])
    assert r.status_code == 503 and "spend limit" in r.json()["detail"]
    assert client.calls == client.beta_calls == []
    assert svc.get("/v1/clinics/me/stats").json()      # nothing stored, service still up


def test_evaluate_runs_and_is_costed_under_the_limit(make_service, hl_data):
    svc = make_service(backend=api_backend(FakeAnthropic()), spend_limit_usd=5.0)
    assert svc.evaluate(hl_data[0]).status_code == 200
    s = svc.app.state.budget.summary()
    assert s["settled_usd"] > 0 and s["open_reservations"] == 0


def test_worker_leaves_conversations_queued_when_the_limit_is_reached(make_service, hl_data):
    # each conversation really costs $0.30 here; its worst case is about $0.34
    client = FakeAnthropic(live=lambda **kw: message(reply(), usage=(100_000, 10_000, 0, 0)))
    svc = make_service(backend=api_backend(client), spend_limit_usd=0.75)   # room for two
    batch = submit(svc, hl_data[:3])
    process_pending(svc.engine, _backend(svc), limit=10)
    body = results_of(svc, batch)
    assert body["status"] != "done" and sum(r["result"] is not None for r in body["results"]) == 2
    assert svc.app.state.budget.summary()["remaining_usd"] >= 0


def _backend(svc):
    return BudgetedBackend(svc.backend, svc.app.state.budget)


def test_batch_api_sends_budget_sized_chunks_and_settles_each(make_service, hl_data):
    client = FakeAnthropic()
    # worst case per conversation at batch price is about $0.085: room for one at a time
    svc = make_service(backend=api_backend(client), spend_limit_usd=0.12, use_batch_api=True)
    backend = _backend(svc)
    batch = submit(svc, hl_data[:3])
    process_batch_api(svc.engine, backend)                  # first chunk only: the rest does not fit
    first = len(client.created[0])
    assert first == 1
    client.status = "ended"
    process_batch_api(svc.engine, backend)                  # collect + settle, remainder re-queued
    assert svc.app.state.budget.summary()["open_reservations"] == 0
    for _ in range(4):
        process_batch_api(svc.engine, backend)
    assert results_of(svc, batch)["status"] == "done"
    assert sum(len(c) for c in client.created) == 3 and client.calls == []    # nothing judged live
    assert svc.app.state.budget.summary()["remaining_usd"] >= 0


def test_new_batches_are_refused_when_there_is_no_room_for_one_conversation(make_service, hl_data):
    svc = make_service(backend=api_backend(FakeAnthropic()), spend_limit_usd=0.05)
    r = svc.post("/v1/batches", json={"conversations": hl_data[:2]})
    assert r.status_code == 503


# ------------------------------------------------------------------ startup and admin

def test_production_needs_a_spend_limit_and_a_priced_model(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    env = {"ANTHROPIC_API_KEY": "x"}
    with pytest.raises(StartupError, match="SPEND_LIMIT_USD"):
        production_backend(Settings.from_env({}), env=env)
    with pytest.raises(StartupError, match="no price"):
        production_backend(Settings.from_env({"SPEND_LIMIT_USD": "20", "MODEL": "claude-unknown"}), env=env)
    assert production_backend(Settings.from_env({"SPEND_LIMIT_USD": "20"}), env=env)


def test_admin_spend_report(engine, capsys):
    Budget(engine, 20).reserve(0.5, "call", "claude-sonnet-5-5")
    admin.print_spend(engine, 20.0)
    out = capsys.readouterr().out
    assert "limit 20.00" in out and "reserved 0.50" in out and "remaining 19.50" in out
