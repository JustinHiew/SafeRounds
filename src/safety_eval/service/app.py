"""The FastAPI app (roadmap 2C).

    uvicorn safety_eval.service.app:get_app --factory

Rules that hold on every route:
- Auth first: `Authorization: Bearer sek_...`, one key = one clinic. Anything that belongs to
  another clinic is a 404, never a 403, so a clinic cannot even learn that an id exists.
- Every conversation is de-identified BEFORE it is stored or sent to the model. The mapping the
  de-identifier returns is thrown away on the spot: it is never stored and never logged.
- The judge fails closed. A model failure, a bad answer or any unexpected exception is stored and
  returned as needs_review, never as a pass.
- Nothing logs conversation text, record values or model output.
"""

import logging
from collections import Counter
from datetime import datetime, time, timedelta, timezone

import sqlalchemy as sa
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from safety_eval.clinic import Clinic
from safety_eval.schemas import CRITERIA, BaseConversation, JudgeResult

from . import auth as auth_mod
from .db import (as_utc, batches, conversations, feedback, insert_conversation, insert_result,
                 judge_results, make_engine, new_batch_id, utcnow, write_audit)
from .guard import Guard, RateLimiter
from .judging import PromptError, judge_safely, load_prompt
from .models import (BatchCreated, BatchItem, BatchRequest, BatchStatus, ConversationOut, DayStats,
                     EvaluateRequest, EvaluateResponse, FeedbackRequest, FeedbackResponse, Rejected,
                     StatsResponse)
from .settings import (Settings, StartupError, production_backend, production_deidentifier,
                       require_session_secret)
from .ui import make_ui_router

log = logging.getLogger("safety_eval.service")


class DeidentificationFailed(RuntimeError):
    pass


def validation_reasons(exc):
    """Why a conversation was refused. Built from field names and messages only: pydantic's own
    text repeats the offending input, which here is conversation text."""
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return [str(exc)] if isinstance(exc, ValueError) else ["invalid conversation"]


def intake(clinic, data, known_names, deidentify):
    """(de-identified conversation, None) or (None, reasons). Raises DeidentificationFailed if the
    de-identifier itself breaks: the conversation is then dropped, never stored as it came."""
    try:
        conv = clinic.parse_conversation(data)
    except Exception as exc:
        return None, validation_reasons(exc)
    try:
        clean, _mapping = deidentify(conv, clinic, tuple(known_names))   # mapping discarded here
        if not isinstance(clean, BaseConversation):
            raise TypeError("de-identifier returned the wrong type")
        BaseConversation.model_validate(clean.model_dump())    # still a valid conversation after masking
    except Exception as exc:
        log.error("de-identification failed (%s)", type(exc).__name__)
        raise DeidentificationFailed from exc
    return clean, None


def create_app(settings, backend, deidentify):
    """Build the app. `deidentify(conv, clinic, known_names=()) -> (conversation, mapping)` is
    required: there is no way to construct the service without one."""
    if not callable(deidentify):
        raise StartupError("a de-identifier is required")
    load_prompt(settings.prompt_version)      # fail at startup, not on the first request
    engine = make_engine(settings.database_url)
    limiter = RateLimiter()
    app = FastAPI(title="Conversation safety API", version="1")
    app.add_middleware(Guard, default_limit=settings.max_other_bytes, limits=[
        (("POST", "/v1/evaluate"), settings.max_evaluate_bytes),
        (("POST", "/v1/batches"), settings.max_batch_bytes)])
    app.state.engine = engine

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        return JSONResponse({"detail": [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                                        for e in exc.errors()]}, status_code=422)

    # ------------------------------------------------------------------ auth and limits

    class Caller:
        def __init__(self, clinic_id, prefix):
            self.clinic_id, self.prefix = clinic_id, prefix

        @property
        def clinic(self):
            return Clinic.load(self.clinic_id)

    def caller(authorization: str | None = Header(default=None)):
        token = auth_mod.bearer_token(authorization)
        found = None
        if token:
            with engine.connect() as conn:
                found = auth_mod.lookup(conn, token)
        if not found:   # the same answer for missing, malformed, unknown and revoked keys
            raise HTTPException(401, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})
        return Caller(*found)

    def limited(bucket, per_minute):
        def check(who: Caller = Depends(caller)):
            if not limiter.allow((who.prefix, bucket), per_minute):
                raise HTTPException(429, "rate limit exceeded, slow down", headers={"Retry-After": "60"})
            return who
        return check

    def prompt_for(version):
        try:
            return load_prompt(settings.prompt_version if version is None else version).version
        except PromptError as exc:
            raise HTTPException(422, str(exc)) from None

    # ------------------------------------------------------------------ routes

    @app.get("/healthz")
    def healthz():
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("select 1"))
        except Exception:
            raise HTTPException(503, "database unavailable") from None
        return {"status": "ok"}

    @app.post("/v1/evaluate", response_model=EvaluateResponse)
    def evaluate(body: EvaluateRequest, who: Caller = Depends(limited("evaluate", settings.evaluate_per_minute))):
        version = prompt_for(body.prompt_version)
        clean, reasons = intake(who.clinic, body.conversation, body.known_names, deidentify)
        if reasons:
            raise HTTPException(422, reasons)
        with engine.begin() as conn:
            pk = insert_conversation(conn, who.clinic_id, clean)
        result = judge_safely(clean.model_dump(), who.clinic_id, version, backend)
        with engine.begin() as conn:
            result_id = insert_result(conn, who.clinic_id, pk, result)
        log.info("evaluated clinic=%s result=%s outcome=%s", who.clinic_id, result_id, result.outcome)
        return EvaluateResponse(result_id=result_id, conversation_pk=pk, result=result)

    @app.post("/v1/batches", response_model=BatchCreated, status_code=202)
    def create_batch(body: BatchRequest, who: Caller = Depends(limited("batches", settings.batches_per_minute))):
        version = prompt_for(body.prompt_version)
        clinic, accepted, rejected, seen = who.clinic, [], [], set()
        for i, data in enumerate(body.conversations):
            cid = data.get("conversation_id") if isinstance(data, dict) else None
            cid = cid[:100] if isinstance(cid, str) else None
            try:
                clean, reasons = intake(clinic, data, body.known_names, deidentify)
            except DeidentificationFailed:
                clean, reasons = None, ["de-identification failed"]
            if clean is not None and clean.conversation_id in seen:
                clean, reasons = None, ["duplicate conversation_id in this batch"]
            if reasons:
                rejected.append(Rejected(index=i, conversation_id=cid, reasons=reasons))
                continue
            seen.add(clean.conversation_id)
            accepted.append(clean)
        batch_id, status = new_batch_id(), ("queued" if accepted else "done")
        with engine.begin() as conn:
            conn.execute(batches.insert().values(
                id=batch_id, clinic_id=who.clinic_id, status=status, prompt_version=version,
                n_submitted=len(accepted), n_rejected=len(rejected), created_at=utcnow(),
                finished_at=None if accepted else utcnow()))
            for clean in accepted:
                insert_conversation(conn, who.clinic_id, clean, batch_id)
        log.info("batch %s clinic=%s accepted=%d rejected=%d", batch_id, who.clinic_id,
                 len(accepted), len(rejected))
        return BatchCreated(batch_id=batch_id, status=status, submitted=len(accepted), rejected=rejected)

    @app.get("/v1/batches/{batch_id}", response_model=BatchStatus)
    def get_batch(batch_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                  who: Caller = Depends(caller)):
        with engine.begin() as conn:
            batch = conn.execute(sa.select(batches).where(
                batches.c.id == batch_id, batches.c.clinic_id == who.clinic_id)).first()
            if batch is None:
                raise HTTPException(404, "batch not found")
            joined = sa.outerjoin(conversations, judge_results,
                                  judge_results.c.conversation_pk == conversations.c.pk)
            scope = sa.and_(conversations.c.batch_id == batch_id, conversations.c.clinic_id == who.clinic_id)
            rows = conn.execute(
                sa.select(conversations.c.pk, conversations.c.conversation_id,
                          judge_results.c.pk.label("result_id"), judge_results.c.result)
                .select_from(joined).where(scope).order_by(conversations.c.pk)
                .offset(offset).limit(limit)).all()
            tally = conn.execute(
                sa.select(judge_results.c.outcome, judge_results.c.flagged, sa.func.count())
                .select_from(joined).where(scope, judge_results.c.pk.is_not(None))
                .group_by(judge_results.c.outcome, judge_results.c.flagged)).all()
            write_audit(conn, who.clinic_id, who.prefix, "read_batch_results", batch_id)   # results quote content
        by_outcome = Counter()
        for outcome, _, n in tally:
            by_outcome[outcome] += n
        return BatchStatus(
            batch_id=batch.id, status=batch.status, prompt_version=batch.prompt_version,
            submitted=batch.n_submitted, rejected=batch.n_rejected, completed=sum(by_outcome.values()),
            by_outcome={o: by_outcome.get(o, 0) for o in ("ok", "unsure", "needs_review")},
            flagged=sum(n for _, flagged, n in tally if flagged),
            created_at=as_utc(batch.created_at),
            finished_at=as_utc(batch.finished_at) if batch.finished_at else None,
            offset=offset, limit=limit,
            results=[BatchItem(conversation_pk=r.pk, conversation_id=r.conversation_id, result_id=r.result_id,
                               result=JudgeResult.model_validate(r.result) if r.result else None)
                     for r in rows])

    @app.post("/v1/feedback", response_model=FeedbackResponse, status_code=201)
    def add_feedback(body: FeedbackRequest, who: Caller = Depends(caller)):
        with engine.begin() as conn:
            if not conn.execute(sa.select(judge_results.c.pk).where(
                    judge_results.c.pk == body.result_id, judge_results.c.clinic_id == who.clinic_id)).first():
                raise HTTPException(404, "result not found")
            pk = conn.execute(feedback.insert().values(
                clinic_id=who.clinic_id, result_pk=body.result_id, reviewer=body.reviewer,
                criterion=body.criterion, action=body.action, note=body.note,
                created_at=utcnow())).inserted_primary_key[0]
        return FeedbackResponse(feedback_id=pk)

    @app.get("/v1/clinics/me/stats", response_model=StatsResponse)
    def stats(days: int = Query(28, ge=1, le=365), who: Caller = Depends(caller)):
        start = utcnow().date() - timedelta(days=days - 1)
        since = datetime.combine(start, time.min, tzinfo=timezone.utc)
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(judge_results.c.created_at, judge_results.c.outcome, judge_results.c.flagged,
                          judge_results.c.result)
                .where(judge_results.c.clinic_id == who.clinic_id,
                       judge_results.c.created_at >= since)).all()
        daily = {(start + timedelta(days=i)).isoformat(): DayStats(
            date=(start + timedelta(days=i)).isoformat(), criterion_fails={c: 0 for c in CRITERIA})
            for i in range(days)}
        for r in rows:
            day = daily.get(as_utc(r.created_at).date().isoformat())
            if day is None:
                continue
            day.evaluated += 1
            day.flagged += bool(r.flagged)
            day.unsure += r.outcome == "unsure"
            day.needs_review += r.outcome == "needs_review"
            for crit, j in ((r.result.get("output") or {}).get("criteria") or {}).items():
                if j.get("verdict") == "fail" and crit in day.criterion_fails:
                    day.criterion_fails[crit] += 1
        return StatsResponse(days=days, daily=list(daily.values()))

    @app.get("/v1/conversations/{pk}", response_model=ConversationOut)
    def get_conversation(pk: int, who: Caller = Depends(caller)):
        with engine.begin() as conn:
            row = conn.execute(sa.select(conversations).where(
                conversations.c.pk == pk, conversations.c.clinic_id == who.clinic_id)).first()
            if row is None:
                raise HTTPException(404, "conversation not found")
            write_audit(conn, who.clinic_id, who.prefix, "read_conversation", pk)
        return ConversationOut(conversation_pk=row.pk, conversation_id=row.conversation_id,
                               batch_id=row.batch_id, conversation=row.deidentified,
                               created_at=as_utc(row.created_at))

    if settings.session_secret:      # the reviewer UI (2D); get_app() makes the secret mandatory
        app.include_router(make_ui_router(engine, settings))
    return app


def get_app():
    """The production app: real de-identifier, real model. Fails at startup, with a clear
    message, if either is missing. Use this as the uvicorn factory."""
    settings = Settings.from_env()
    deidentify = production_deidentifier()      # checked first: no de-identifier, no service
    backend = production_backend(settings)
    require_session_secret(settings)
    return create_app(settings, backend, deidentify)


_production_app = None


def __getattr__(name):
    """`uvicorn safety_eval.service.app:app` builds the production app on first use. Importing
    this module (as the tests do) builds nothing, so it needs no key and no de-identifier."""
    global _production_app
    if name == "app":
        _production_app = _production_app or get_app()
        return _production_app
    raise AttributeError(name)
