"""Prompt caching (cache_prefix, render_parts, usage, refusal fallbacks) and the Message Batches API
path of the worker. No real client is ever built: every Anthropic client here is a fake."""

import json
import logging
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from conftest import PHRASE, reply
from safety_eval import batch_api
from safety_eval import judge as J
from safety_eval.labelling import load_conversations
from safety_eval.llm import FALLBACK_BETA, ApiBackend, FakeBackend, ModelCallError
from safety_eval.schemas import CRITERIA
from safety_eval.service.db import batches, judge_results
from safety_eval.service.settings import Settings, production_backend
from safety_eval.service.worker import process_batch_api, process_pending

# ------------------------------------------------------------------------------ fakes


def message(text="hello", stop="end_turn", model="claude-sonnet-5-5", usage=(10, 5, 100, 20)):
    i, o, read, write = usage
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)] if text is not None else [],
        model=model, stop_reason=stop,
        usage=SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=read,
                              cache_creation_input_tokens=write))


class FakeAnthropic:
    """messages.create, beta.messages.create and messages.batches.create/retrieve/results."""

    def __init__(self, live=None, batch_answer=None, reverse=True):
        self.live = live or (lambda **kw: message(reply()))
        self.batch_answer = batch_answer or (lambda custom_id, params: ("succeeded", message(reply())))
        self.reverse = reverse
        self.calls, self.beta_calls, self.created = [], [], []
        self.status, self.fail_create = "in_progress", False
        self.messages = SimpleNamespace(
            create=self._create,
            batches=SimpleNamespace(create=self._batch_create, retrieve=self._retrieve,
                                    results=self._results))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta_create))

    def _create(self, **kw):
        self.calls.append(kw)
        return self.live(**kw)

    def _beta_create(self, **kw):
        self.beta_calls.append(kw)
        return self.live(**kw)

    def _batch_create(self, requests):
        if self.fail_create:
            raise RuntimeError(f"down {PHRASE}")
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def _retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self.status)

    def _results(self, batch_id):
        requests = self.created[int(batch_id.split("_")[1]) - 1]
        out = []
        for req in requests:
            kind, payload = self.batch_answer(req["custom_id"], req["params"])
            result = SimpleNamespace(type=kind, message=payload) if kind == "succeeded" \
                else SimpleNamespace(type=kind)
            out.append(SimpleNamespace(custom_id=req["custom_id"], result=result))
        return iter(out[::-1] if self.reverse else out)


def api_backend(client, **kw):
    return ApiBackend(model="claude-sonnet-5-5", client=client, **kw)


# ------------------------------------------------------------------------------ render_parts


@pytest.mark.parametrize("version", ["B", "C", "D", "E"])
def test_render_parts_reassemble_to_render_for_every_prompt(version):
    convs = load_conversations(core_only=False)[:6]
    examples = J.EXAMPLES.read_text()
    prompt = J.PromptVersion.load(version, check_leaks=False)
    sets = [list(CRITERIA)] if prompt.mode == "whole" else [[c] for c in CRITERIA]
    for conv in convs:
        for crits in sets:
            stable, rest = J.render_parts(prompt, conv, crits, examples)
            assert stable + rest == J.render(prompt, conv, crits, examples)
            assert stable and conv.turns[0].text not in stable
            if prompt.mode == "whole":      # the same for every conversation: that is what caches
                assert stable == J.render_parts(prompt, convs[0], crits, examples)[0]


def test_render_parts_split_at_the_first_varying_placeholder(tmp_path):
    (tmp_path / "X.md").write_text(
        "---\nversion: X\nmode: per_criterion\n---\n<system>\nsys\n</system>\n"
        "<user>\nRubric: {{RUBRIC}}\nJudge only {{CRITERIA}}.\nRecord: {{PATIENT_RECORD}}\n"
        "{{CONVERSATION}}\n</user>\n")
    prompt = J.PromptVersion.load("X", folder=tmp_path, check_leaks=False)
    conv = load_conversations()[0]
    stable, rest = J.render_parts(prompt, conv, ["safety"])
    assert stable.endswith("Judge only ") and rest.startswith("safety.")
    assert stable + rest == J.render(prompt, conv, ["safety"])


def test_judge_passes_the_stable_part_as_cache_prefix():
    conv = load_conversations()[0]
    prompt = J.PromptVersion.load("C", check_leaks=False)
    fake = FakeBackend(reply())
    from safety_eval.service.judging import NullCache
    J.judge(conv, prompt, fake, NullCache())
    (system, text), = fake.calls
    stable, rest = J.render_parts(prompt, conv)
    assert fake.prefixes == [stable] and text == stable + rest == J.render(prompt, conv)


# ------------------------------------------------------------------------------ ApiBackend


def test_api_backend_sends_cached_blocks_and_records_usage():
    client = FakeAnthropic(live=lambda **kw: message("answer"))
    resp = api_backend(client).complete("SYS", "PREFIX|rest", cache_prefix="PREFIX|")
    (kw,) = client.calls
    assert kw["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "PREFIX|", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "rest"}]}]
    assert "output_config" not in kw and not client.beta_calls
    assert resp.text == "answer" and resp.model == "claude-sonnet-5-5"
    assert resp.raw["usage"] == {"input_tokens": 10, "output_tokens": 5,
                                 "cache_creation_input_tokens": 20, "cache_read_input_tokens": 100}


def test_api_backend_without_prefix_and_with_effort():
    client = FakeAnthropic()
    api_backend(client, effort="medium").complete("SYS", "whole prompt")
    kw = client.calls[0]
    assert kw["messages"][0]["content"] == [{"type": "text", "text": "whole prompt"}]
    assert kw["output_config"] == {"effort": "medium"}
    # A prompt that is entirely prefix never sends an empty text block (the API rejects those).
    api_backend(client).complete("SYS", "all", cache_prefix="all")
    assert client.calls[1]["messages"][0]["content"] == [
        {"type": "text", "text": "all", "cache_control": {"type": "ephemeral"}}]
    with pytest.raises(ModelCallError):
        api_backend(client).complete("SYS", "abc", cache_prefix="zzz")


def test_the_text_the_model_receives_is_unchanged():
    conv = load_conversations()[0]
    prompt = J.PromptVersion.load("C", check_leaks=False)
    client = FakeAnthropic()
    from safety_eval.service.judging import NullCache
    J.judge(conv, prompt, api_backend(client), NullCache())
    kw = client.calls[0]
    assert kw["system"][0]["text"] == prompt.system
    assert "".join(b["text"] for b in kw["messages"][0]["content"]) == J.render(prompt, conv)


def test_fallbacks_use_the_beta_endpoint_with_the_default_form():
    client = FakeAnthropic(live=lambda **kw: message("ok", model="claude-sonnet-5"))
    resp = api_backend(client, fallbacks=True).complete("S", "p", cache_prefix="p")
    (kw,) = client.beta_calls
    assert kw["betas"] == [FALLBACK_BETA] == ["server-side-fallback-2026-07-01"]
    assert kw["fallbacks"] == "default" and not client.calls
    assert resp.model == "claude-sonnet-5"        # the model that actually answered is recorded
    # Off: the ordinary endpoint with no fallbacks parameter. Haiku has no server-side fallback.
    client = FakeAnthropic()
    api_backend(client, fallbacks=False).complete("S", "p")
    ApiBackend(model="claude-haiku-5-5", client=client, fallbacks=True).complete("S", "p")
    assert len(client.calls) == 2 and not client.beta_calls and "fallbacks" not in client.calls[1]


@pytest.mark.parametrize("fallbacks", [False, True])
@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_a_final_refusal_still_fails_closed(fallbacks, stop):
    client = FakeAnthropic(live=lambda **kw: message(None, stop=stop))
    with pytest.raises(ModelCallError):
        api_backend(client, fallbacks=fallbacks).complete("S", "p")
    conv = load_conversations()[0]
    prompt = J.PromptVersion.load("C", check_leaks=False)
    from safety_eval.service.judging import NullCache
    r = J.judge(conv, prompt, api_backend(client, fallbacks=fallbacks), NullCache())
    assert r.outcome == "needs_review" and not r.flagged


def test_settings_and_production_backend(monkeypatch):
    s = Settings.from_env({"EFFORT": "low", "FALLBACKS": "false", "USE_BATCH_API": "1"})
    assert (s.effort, s.fallbacks, s.use_batch_api) == ("low", False, True)
    d = Settings.from_env({})
    assert (d.effort, d.fallbacks, d.use_batch_api) == (None, True, False)
    from safety_eval.service.settings import StartupError
    with pytest.raises(StartupError):
        Settings.from_env({"USE_BATCH_API": "maybe"})
    monkeypatch.setitem(__import__("sys").modules, "anthropic",
                        SimpleNamespace(Anthropic=lambda: FakeAnthropic()))
    b = production_backend(d, env={"ANTHROPIC_API_KEY": "x"})
    assert b.fallbacks and b.effort is None and b.model == "claude-sonnet-5-5"


# ------------------------------------------------------------------------------ usage stored


def submit(svc, convs, **extra):
    r = svc.post("/v1/batches", json={"conversations": convs, **extra})
    assert r.status_code == 202
    return r.json()["batch_id"]


def result_rows(svc):
    with svc.engine.connect() as conn:
        return conn.execute(sa.select(judge_results).order_by(judge_results.c.pk)).all()


def test_evaluate_stores_token_counts_only(make_service, hl_data):
    svc = make_service(backend=api_backend(FakeAnthropic()))
    assert svc.evaluate(hl_data[1]).status_code == 200
    (row,) = result_rows(svc)
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens) == \
        (10, 5, 100, 20)


def test_usage_is_null_for_backends_that_report_none(svc, hl_data):
    svc.evaluate(hl_data[1])
    (row,) = result_rows(svc)
    assert row.input_tokens is None and row.cache_read_tokens is None


# ------------------------------------------------------------------------------ batch API unit


def test_collect_keys_by_custom_id_and_rejects_unusable_answers():
    answers = {"a": ("succeeded", message("A")), "b": ("errored", None), "c": ("canceled", None),
               "d": ("expired", None), "e": ("succeeded", message(None, stop="refusal")),
               "f": ("succeeded", message("cut", stop="max_tokens"))}
    client = FakeAnthropic(batch_answer=lambda cid, params: answers[cid])
    reqs = batch_api.build_requests([(k, "sys", "p", "p") for k in answers], "claude-sonnet-5-5", 100)
    bid = batch_api.submit(client, reqs)
    assert batch_api.poll(client, bid) == "in_progress"
    out = batch_api.collect(client, bid)
    assert out["a"][:3] == ("succeeded", "A", "claude-sonnet-5-5") and out["a"][3]["input_tokens"] == 10
    assert [out[k][0] for k in "bcdef"] == ["errored", "canceled", "expired", "errored", "errored"]
    assert all(out[k][1] is None for k in "bcdef")
    # Same params as the synchronous backend, and never the (rejected) fallbacks parameter.
    sync = ApiBackend(model="claude-sonnet-5-5", max_tokens=100, client=client,
                      fallbacks=True).params("sys", "p", "p")
    assert reqs[0]["params"] == sync and "fallbacks" not in reqs[0]["params"]


def test_replay_backend_answers_once_then_goes_live():
    live = FakeBackend("live answer")
    h = batch_api.prompt_hash("sys", "prompt")
    replay = batch_api.ReplayBackend({h: ("batched", "m-batch", {"input_tokens": 1})}, live)
    first = replay.complete("sys", "prompt", cache_prefix="pro")
    assert (first.text, first.model, first.raw["usage"]) == ("batched", "m-batch", {"input_tokens": 1})
    assert replay.complete("sys", "prompt").text == "live answer"     # the retry goes live
    assert replay.complete("sys", "other").text == "live answer"
    assert len(live.calls) == 2


# ------------------------------------------------------------------------------ worker, batch mode


def batch_status(svc, batch_id):
    with svc.engine.connect() as conn:
        return conn.execute(sa.select(batches.c.status, batches.c.anthropic_batch_id)
                            .where(batches.c.id == batch_id)).one()


def results_of(svc, batch_id):
    return svc.get(f"/v1/batches/{batch_id}").json()


def test_batch_mode_happy_path_out_of_order(make_service, hl_data):
    quote = hl_data[1]["turns"][1]["text"][:30]

    def answer(cid, params):
        # Distinct answers per conversation prove results are matched by custom_id, not by position.
        if cid.startswith("c2-"):
            return "succeeded", message(reply({"scope": "fail"}, quote), usage=(7, 3, 0, 0))
        return "succeeded", message(reply(), usage=(8, 4, 50, 0))

    client = FakeAnthropic(batch_answer=answer, reverse=True)
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0], hl_data[1], hl_data[2]])

    assert process_batch_api(svc.engine, svc.backend) == 1          # submitted
    assert len(client.created) == 1 and len(client.created[0]) == 3
    assert batch_status(svc, batch) == ("running", "msgbatch_1")
    assert process_batch_api(svc.engine, svc.backend) == 0          # still in progress: nothing to do
    assert len(client.created) == 1 and results_of(svc, batch)["completed"] == 0

    client.status = "ended"
    assert process_batch_api(svc.engine, svc.backend) == 3
    body = results_of(svc, batch)
    assert body["status"] == "done" and body["completed"] == 3
    assert [x["result"]["outcome"] for x in body["results"]] == ["ok"] * 3
    assert [bool(x["result"]["output"]["criteria"]["scope"]["verdict"] == "fail")
            for x in body["results"]] == [False, True, False]
    assert not client.calls                                          # no live call was needed
    rows = result_rows(svc)
    assert [(r.input_tokens, r.cache_read_tokens) for r in rows] == [(8, 50), (7, 0), (8, 50)]
    assert process_batch_api(svc.engine, svc.backend) == 0           # nothing left


def test_batch_requests_carry_the_cached_prefix_and_same_params(make_service, hl_data):
    client = FakeAnthropic()
    svc = make_service(backend=api_backend(client, effort="low"))
    submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    (req,) = client.created[0]
    params = req["params"]
    assert req["custom_id"].startswith("c") and "fallbacks" not in params
    assert params["output_config"] == {"effort": "low"} and params["model"] == "claude-sonnet-5-5"
    first, second = params["messages"][0]["content"]
    assert first["cache_control"] == {"type": "ephemeral"} and "cache_control" not in second
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_errored_expired_and_canceled_items_get_one_live_attempt(make_service, hl_data):
    kinds = {"c1": "errored", "c2": "expired", "c3": "canceled"}

    def answer(cid, params):
        return kinds[cid.split("-")[0]], None

    client = FakeAnthropic(batch_answer=answer)
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0], hl_data[1], hl_data[2]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    body = results_of(svc, batch)
    assert body["status"] == "done" and len(client.calls) == 3       # one live call each
    assert [x["result"]["outcome"] for x in body["results"]] == ["ok"] * 3


def test_a_live_attempt_that_also_fails_is_needs_review(make_service, hl_data):
    def live(**kw):
        raise RuntimeError(f"api down {PHRASE}")

    client = FakeAnthropic(live=live, batch_answer=lambda cid, params: ("errored", None))
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    body = results_of(svc, batch)
    assert body["status"] == "done" and body["results"][0]["result"]["outcome"] == "needs_review"
    assert body["flagged"] == 0


def test_malformed_batch_answer_gets_the_judges_live_retry(make_service, hl_data):
    client = FakeAnthropic(batch_answer=lambda cid, params: ("succeeded", message("not json at all")),
                           live=lambda **kw: message(reply()))
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    result = results_of(svc, batch)["results"][0]["result"]
    assert result["outcome"] == "ok" and result["attempts"] == 2 and len(client.calls) == 1
    # And if the retry is malformed too, it is needs_review, exactly as on the synchronous path.
    client = FakeAnthropic(batch_answer=lambda cid, params: ("succeeded", message("junk")),
                           live=lambda **kw: message("still junk"))
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    assert results_of(svc, batch)["results"][0]["result"]["outcome"] == "needs_review"


def test_a_bad_quote_in_a_batch_answer_is_needs_review(make_service, hl_data):
    client = FakeAnthropic(batch_answer=lambda cid, params: (
        "succeeded", message(reply({"scope": "fail"}, "words that were never said"))))
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[1]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    assert results_of(svc, batch)["results"][0]["result"]["outcome"] == "needs_review"


def test_refusal_in_a_batch_is_needs_review(make_service, hl_data):
    refused = message(None, stop="refusal")
    client = FakeAnthropic(batch_answer=lambda cid, params: ("succeeded", refused),
                           live=lambda **kw: refused)
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0], hl_data[1]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    body = results_of(svc, batch)
    assert body["status"] == "done" and body["flagged"] == 0
    assert [x["result"]["outcome"] for x in body["results"]] == ["needs_review"] * 2
    assert len(client.calls) == 2         # each refused item got its one live attempt (also refused)


def test_worker_restart_mid_batch(make_service, hl_data):
    client = FakeAnthropic()
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0], hl_data[1]])
    process_batch_api(svc.engine, svc.backend)

    # "Restart": a brand new backend object and nothing carried in memory. It must not resubmit.
    fresh = api_backend(client)
    assert process_batch_api(svc.engine, fresh) == 0
    assert len(client.created) == 1
    client.status = "ended"
    # A restart half way through judging: one result already recorded, the rest still pending.
    assert process_pending(svc.engine, FakeBackend(reply()), limit=1) == 1
    assert process_batch_api(svc.engine, api_backend(client)) == 1
    body = results_of(svc, batch)
    assert body["status"] == "done" and body["completed"] == 2 and len(client.created) == 1


def test_per_criterion_prompts_batch_one_request_per_criterion(make_service, hl_data):
    def answer(cid, params):
        crit = cid.rsplit("-", 1)[1]
        return "succeeded", message(json.dumps({"criteria": {crit: {
            "evidence": [], "analysis": "x", "verdict": "pass"}}, "severity": "none"}))

    client = FakeAnthropic(batch_answer=answer)
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0]], prompt_version="E")
    process_batch_api(svc.engine, svc.backend)
    assert sorted(r["custom_id"].rsplit("-", 1)[1] for r in client.created[0]) == sorted(CRITERIA)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    assert results_of(svc, batch)["results"][0]["result"]["outcome"] == "ok" and not client.calls


def test_submit_failure_falls_back_to_live_judging(make_service, hl_data):
    client = FakeAnthropic()
    client.fail_create = True
    svc = make_service(backend=api_backend(client))
    batch = submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    body = results_of(svc, batch)
    assert body["status"] == "done" and body["results"][0]["result"]["outcome"] == "ok"
    assert batch_status(svc, batch)[1] is None and len(client.calls) == 1


def test_batch_mode_logs_never_contain_text(make_service, hl_data, caplog):
    caplog.set_level(logging.DEBUG)
    client = FakeAnthropic(batch_answer=lambda cid, params: ("succeeded", message("junk")),
                           live=lambda **kw: (_ for _ in ()).throw(RuntimeError(f"x {PHRASE}")))
    client.fail_create = False
    svc = make_service(backend=api_backend(client))
    submit(svc, [hl_data[0]])
    process_batch_api(svc.engine, svc.backend)
    client.status = "ended"
    process_batch_api(svc.engine, svc.backend)
    assert PHRASE not in caplog.text and "junk" not in caplog.text


def test_sync_worker_is_unchanged_by_default(svc, hl_data):
    svc.post("/v1/batches", json={"conversations": [hl_data[1]]})
    assert process_pending(svc.engine, svc.backend) == 1
    assert svc.backend.prefixes[0] and svc.backend.calls[0][1].startswith(svc.backend.prefixes[0])
