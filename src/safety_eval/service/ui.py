"""The reviewer queue web app (roadmap 2D): login, a review page, and the /ui/api/* it talks to.

- Sessions are an HMAC-SHA256-signed cookie (no session table, no new dependency). It carries the
  reviewer id, an expiry and a random session id. Disabling a reviewer ends their session at once
  because the reviewer row is checked on every request.
- CSRF: every state-changing /ui/api request needs `X-CSRF-Token` = HMAC(secret, session id). The
  page reads it from the non-HttpOnly `se_csrf` cookie.
- Every query filters on the reviewer's clinic_id; another clinic's id is a 404.
- Content reads are audited (audit_log, api_key_prefix = "reviewer:<id>").
- The dashboard and /ui/api/metrics (2F) are admin-only and show the clinic's own numbers; never spend.
- Nothing here logs conversation text, notes or passwords.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from safety_eval.clinic import Clinic
from safety_eval.schemas import CRITERIA, Criterion

from . import monitoring
from .db import as_utc, conversations, feedback, judge_results, utcnow, write_audit
from .guard import RateLimiter
from .ui_db import DUMMY_HASH, normalise_email, reviewers, verify_password

SESSION_SECONDS = 8 * 3600
SESSION_COOKIE, CSRF_COOKIE = "se_session", "se_csrf"
STATIC = Path(__file__).parent / "static"
clock = time.time          # tests replace this to move time


# ------------------------------------------------------------------ tokens

def _sig(secret, label, payload):
    mac = hmac.new(secret.encode(), f"{label}:{payload}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_session_token(secret, reviewer_id, expires=None, nonce=None):
    expires = int(clock() + SESSION_SECONDS) if expires is None else int(expires)
    payload = f"{int(reviewer_id)}.{expires}.{nonce or secrets.token_urlsafe(16)}"
    return f"{payload}.{_sig(secret, 'session', payload)}"


def read_session_token(secret, token):
    """(reviewer_id, session id) for a genuine, unexpired token, else None."""
    try:
        payload, sig = token.rsplit(".", 1)
        rid, expires, nonce = payload.split(".", 2)
        if not hmac.compare_digest(sig, _sig(secret, "session", payload)):
            return None
        if int(expires) < clock():
            return None
        return int(rid), nonce
    except (ValueError, AttributeError):
        return None


def csrf_for(secret, session_id):
    return _sig(secret, "csrf", session_id)


# ------------------------------------------------------------------ shapes

class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clinic: str = Field(max_length=100)
    email: str = Field(max_length=200)
    password: str = Field(max_length=500)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: Criterion | None = None
    action: Literal["confirm", "overturn", "comment"]
    note: str = Field(default="", max_length=4000)


class Who:
    def __init__(self, row, session_id):
        self.id, self.clinic_id, self.email, self.role = row.id, row.clinic_id, row.email, row.role
        self.session_id = session_id

    @property
    def audit_prefix(self):
        return f"reviewer:{self.id}"


# ------------------------------------------------------------------ helpers

def _criteria(result):
    """criterion -> judgement dict, or {} when the judge produced no usable output."""
    return ((result or {}).get("output") or {}).get("criteria") or {}


def _reviewed_clause():
    return sa.exists().where(feedback.c.result_pk == judge_results.c.pk,
                             feedback.c.action.in_(("confirm", "overturn")))


def _needs_a_look():
    return sa.or_(judge_results.c.outcome.in_(("needs_review", "unsure")), judge_results.c.flagged)


def corrected_verdicts(result, feedback_rows):
    """(judge verdicts, corrected verdicts, overturned criteria). The latest confirm/overturn on a
    criterion wins; an overturn flips pass<->fail. Whole-result entries (criterion null) change nothing."""
    judge = {c: j.get("verdict") for c, j in _criteria(result).items()}
    last = {}
    for fb in sorted(feedback_rows, key=lambda r: r.pk):
        if fb.criterion and fb.action in ("confirm", "overturn"):
            last[fb.criterion] = fb.action
    flip = {"pass": "fail", "fail": "pass"}
    corrected, overturned = dict(judge), []
    for crit, action in last.items():
        if action == "overturn" and judge.get(crit) in flip:
            corrected[crit] = flip[judge[crit]]
            overturned.append(crit)
    return judge, corrected, [c for c in CRITERIA if c in overturned]


def candidate_line(row, conv, result, feedback_rows):
    """One export record, or None if nothing was overturned. Shaped like HumanLabel plus the
    conversation, but marked as reviewer feedback; it is never a human label."""
    judge, corrected, overturned = corrected_verdicts(result, feedback_rows)
    if not overturned:
        return None
    failing = "fail" in corrected.values()
    judge_sev = ((result.get("output") or {}).get("severity"))
    severity = "none" if not failing else (judge_sev if judge_sev in ("low", "high") else None)
    return {
        "source": "reviewer_feedback",
        "clinic_id": row.clinic_id,
        "result_id": row.pk,
        "conversation_id": conv.conversation_id,
        "conversation": conv.deidentified,
        "judge": {"prompt_version": row.prompt_version, "model": row.model, "outcome": row.outcome,
                  "verdicts": judge, "severity": judge_sev},
        "verdicts": {c: corrected.get(c) for c in CRITERIA},
        "severity": severity,                  # null: a criterion was flipped to fail and the judge gave none
        "unsure": any(v == "unsure" for v in corrected.values()),
        "overturned": overturned,
        "reviewers": sorted({f.reviewer for f in feedback_rows if f.action in ("confirm", "overturn")}),
        "notes": [f.note for f in sorted(feedback_rows, key=lambda r: r.pk) if f.note],
        "labelled_at": as_utc(max(f.created_at for f in feedback_rows)).isoformat(),
    }


def _csp_for(html):
    """A Content-Security-Policy that allows exactly the page's own inline script and style (by
    hash) and same-origin requests: nothing external can load or be contacted."""
    def h(block):
        return "'sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode() + "'"
    scripts = " ".join(h(b) for b in re.findall(r"<script>(.*?)</script>", html, re.DOTALL))
    styles = " ".join(h(b) for b in re.findall(r"<style>(.*?)</style>", html, re.DOTALL))
    return ("default-src 'none'; script-src " + (scripts or "'none'") + "; style-src " + (styles or "'none'")
            + "; connect-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; "
              "frame-ancestors 'none'")


def _page(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    return HTMLResponse(html, headers={
        "Content-Security-Policy": _csp_for(html), "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


# ------------------------------------------------------------------ router

def make_ui_router(engine, settings):
    secret = settings.session_secret
    router = APIRouter()
    login_limiter = RateLimiter()
    LOGIN_PER_MINUTE = 8

    def current(request: Request):
        parsed = read_session_token(secret, request.cookies.get(SESSION_COOKIE, ""))
        row = None
        if parsed:
            with engine.connect() as conn:
                row = conn.execute(sa.select(reviewers).where(
                    reviewers.c.id == parsed[0], reviewers.c.disabled_at.is_(None))).first()
        if row is None:
            raise HTTPException(401, "not signed in")
        return Who(row, parsed[1])

    def mutating(request: Request, who: Who = Depends(current)):
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent.encode(), csrf_for(secret, who.session_id).encode()):
            raise HTTPException(403, "missing or invalid CSRF token")
        return who

    def admin_only(who: Who = Depends(current)):
        if who.role != "admin":
            raise HTTPException(403, "admin only")
        return who

    def signed_in(request):
        try:
            current(request)
            return True
        except HTTPException:
            return False

    def set_cookies(response, session_token, session_id):
        common = dict(path="/ui", samesite="strict", secure=settings.cookie_secure, max_age=SESSION_SECONDS)
        response.set_cookie(SESSION_COOKIE, session_token, httponly=True, **common)
        response.set_cookie(CSRF_COOKIE, csrf_for(secret, session_id), httponly=False, **common)

    # ---- pages

    @router.get("/ui/login", include_in_schema=False)
    def login_page():
        return _page("login.html")

    @router.get("/ui", include_in_schema=False)
    def review_page(request: Request):
        if not signed_in(request):
            return RedirectResponse("/ui/login", status_code=303)
        return _page("review.html")

    @router.get("/ui/dashboard", include_in_schema=False)
    def dashboard_page(request: Request):
        if not signed_in(request):
            return RedirectResponse("/ui/login", status_code=303)
        admin_only(current(request))      # a reviewer gets 403, not the page
        return _page("dashboard.html")

    # ---- session

    @router.post("/ui/login")
    def login(body: Login, request: Request):
        email = normalise_email(body.email)
        ip = request.client.host if request.client else "?"
        if not (login_limiter.allow(("ip", ip), LOGIN_PER_MINUTE * 4)
                and login_limiter.allow((ip, email), LOGIN_PER_MINUTE)):
            raise HTTPException(429, "too many attempts, wait a minute", headers={"Retry-After": "60"})
        with engine.connect() as conn:
            row = conn.execute(sa.select(reviewers).where(
                reviewers.c.clinic_id == body.clinic.strip(), reviewers.c.email == email)).first()
        # Always run one scrypt, so an unknown email takes as long as a wrong password.
        ok = verify_password(body.password, row.password_hash if row else DUMMY_HASH)
        if not (row and ok and row.disabled_at is None):
            raise HTTPException(401, "invalid clinic, email or password")
        token = make_session_token(secret, row.id)
        session_id = read_session_token(secret, token)[1]
        response = JSONResponse({"email": row.email, "role": row.role, "clinic": row.clinic_id})
        set_cookies(response, token, session_id)
        return response

    @router.post("/ui/logout")
    def logout(who: Who = Depends(mutating)):
        response = JSONResponse({"ok": True})
        for name in (SESSION_COOKIE, CSRF_COOKIE):
            response.delete_cookie(name, path="/ui")
        return response

    @router.get("/ui/api/me")
    def me(who: Who = Depends(current)):
        return {"email": who.email, "role": who.role, "clinic": who.clinic_id}

    # ---- queue

    @router.get("/ui/api/queue")
    def queue(state: Literal["open", "reviewed"] = "open",
              outcome: Literal["needs_review", "unsure", "ok"] | None = None,
              offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
              who: Who = Depends(current)):
        reviewed = _reviewed_clause()
        scope = sa.and_(judge_results.c.clinic_id == who.clinic_id, _needs_a_look(),
                        reviewed if state == "reviewed" else ~reviewed)
        if outcome:
            scope = sa.and_(scope, judge_results.c.outcome == outcome)
        with engine.connect() as conn:
            total = conn.execute(sa.select(sa.func.count()).select_from(judge_results).where(scope)).scalar()
            rows = conn.execute(
                sa.select(judge_results.c.pk, judge_results.c.created_at, judge_results.c.outcome,
                          judge_results.c.flagged, judge_results.c.severity, judge_results.c.result,
                          conversations.c.conversation_id)
                .select_from(judge_results.join(conversations,
                                                conversations.c.pk == judge_results.c.conversation_pk))
                .where(scope).order_by(judge_results.c.created_at.desc(), judge_results.c.pk.desc())
                .offset(offset).limit(limit)).all()
            counts = {}
            if rows:
                counts = dict(conn.execute(
                    sa.select(feedback.c.result_pk, sa.func.count()).where(
                        feedback.c.clinic_id == who.clinic_id,
                        feedback.c.result_pk.in_([r.pk for r in rows])).group_by(feedback.c.result_pk)).all())
        items = []
        for r in rows:
            crits = _criteria(r.result)
            items.append({
                "result_id": r.pk, "conversation_id": r.conversation_id,
                "created_at": as_utc(r.created_at).isoformat(), "outcome": r.outcome,
                "flagged": bool(r.flagged), "severity": r.severity,
                "failed": [c for c in CRITERIA if crits.get(c, {}).get("verdict") == "fail"],
                "unsure": [c for c in CRITERIA if crits.get(c, {}).get("verdict") == "unsure"],
                "reviewed": state == "reviewed", "feedback_count": counts.get(r.pk, 0)})
        return {"state": state, "total": total, "offset": offset, "limit": limit, "items": items}

    def own_result(conn, who, result_id):
        row = conn.execute(
            sa.select(judge_results, conversations.c.conversation_id,
                      conversations.c.deidentified.label("deidentified"))
            .select_from(judge_results.join(conversations,
                                            conversations.c.pk == judge_results.c.conversation_pk))
            .where(judge_results.c.pk == result_id, judge_results.c.clinic_id == who.clinic_id)).first()
        if row is None:
            raise HTTPException(404, "result not found")
        return row

    @router.get("/ui/api/results/{result_id}")
    def detail(result_id: int, who: Who = Depends(current)):
        with engine.begin() as conn:
            row = own_result(conn, who, result_id)
            fbs = conn.execute(sa.select(feedback).where(
                feedback.c.result_pk == result_id, feedback.c.clinic_id == who.clinic_id)
                .order_by(feedback.c.pk)).all()
            write_audit(conn, who.clinic_id, who.audit_prefix, "ui_read_result", result_id)
        crits = _criteria(row.result)
        output = row.result.get("output") or {}
        responder = (row.deidentified or {}).get("responder", "")
        try:     # the clinic's own name for the role, e.g. "peer support worker"
            responder_label = Clinic.load(who.clinic_id).staff.get(responder, responder)
        except Exception:
            responder_label = responder
        return {
            "result_id": row.pk, "conversation_id": row.conversation_id, "responder_label": responder_label,
            "conversation": row.deidentified, "outcome": row.outcome, "flagged": bool(row.flagged),
            "severity": row.severity, "reasons": row.result.get("reasons") or [],
            "prompt_version": row.prompt_version, "model": row.model,
            "created_at": as_utc(row.created_at).isoformat(),
            "criteria": [{"name": c, "verdict": crits[c].get("verdict"), "analysis": crits[c].get("analysis", ""),
                          "evidence": crits[c].get("evidence") or []} for c in CRITERIA if c in crits],
            "overall_flag": output.get("overall_flag"),
            "feedback": [{"id": f.pk, "reviewer": f.reviewer, "criterion": f.criterion, "action": f.action,
                          "note": f.note, "created_at": as_utc(f.created_at).isoformat()} for f in fbs],
            "reviewed": any(f.action in ("confirm", "overturn") for f in fbs)}

    @router.post("/ui/api/results/{result_id}/feedback", status_code=201)
    def add_feedback(result_id: int, body: FeedbackIn, who: Who = Depends(mutating)):
        if body.action == "overturn" and body.criterion is None:
            raise HTTPException(422, "an overturn must name a criterion")
        if body.action == "comment" and not body.note.strip():
            raise HTTPException(422, "a comment needs a note")
        with engine.begin() as conn:
            row = own_result(conn, who, result_id)
            if body.action == "overturn":
                verdict = _criteria(row.result).get(body.criterion, {}).get("verdict")
                if verdict not in ("pass", "fail"):
                    raise HTTPException(422, "only a pass or fail verdict can be overturned; "
                                             "use a comment for an unsure one")
            pk = conn.execute(feedback.insert().values(
                clinic_id=who.clinic_id, result_pk=result_id, reviewer=who.email,
                criterion=body.criterion, action=body.action, note=body.note.strip(),
                created_at=utcnow())).inserted_primary_key[0]
            reviewed = bool(conn.execute(sa.select(feedback.c.pk).where(
                feedback.c.result_pk == result_id, feedback.c.action.in_(("confirm", "overturn")))).first())
        return {"feedback_id": pk, "reviewed": reviewed}

    # ---- dashboard (2F): this clinic's own numbers; spend is global and never shown here

    @router.get("/ui/api/metrics")
    def metrics(days: int = Query(28, ge=1, le=90), who: Who = Depends(admin_only)):
        daily = monitoring.clinic_daily(engine, who.clinic_id, days)
        for d in daily:
            d.pop("est_cost_usd")         # what a clinic costs the operator is the operator's business
        return {"clinic": who.clinic_id, "days": days, "daily": daily,
                "alerts": monitoring.clinic_alerts(engine, who.clinic_id, settings),
                "thresholds": {"sigma": settings.alert_sigma, "min_volume": settings.alert_min_volume,
                               "min_shift": settings.alert_min_shift,
                               "needs_review_ceiling": settings.alert_needs_review_ceiling,
                               "overturn_ceiling": settings.alert_overturn_ceiling,
                               "min_reviewed": settings.alert_min_reviewed}}

    # ---- export

    @router.get("/ui/api/export/candidates.jsonl")
    def export(who: Who = Depends(admin_only)):
        with engine.begin() as conn:
            overturned = sa.select(feedback.c.result_pk).where(
                feedback.c.clinic_id == who.clinic_id, feedback.c.action == "overturn")
            rows = conn.execute(
                sa.select(judge_results, conversations.c.conversation_id,
                          conversations.c.deidentified.label("deidentified"))
                .select_from(judge_results.join(conversations,
                                                conversations.c.pk == judge_results.c.conversation_pk))
                .where(judge_results.c.clinic_id == who.clinic_id, judge_results.c.pk.in_(overturned))
                .order_by(judge_results.c.pk)).all()
            fbs = {}
            for f in conn.execute(sa.select(feedback).where(
                    feedback.c.clinic_id == who.clinic_id,
                    feedback.c.result_pk.in_([r.pk for r in rows]))).all() if rows else []:
                fbs.setdefault(f.result_pk, []).append(f)
            write_audit(conn, who.clinic_id, who.audit_prefix, "ui_export_candidates", len(rows))
        lines = []
        for r in rows:
            line = candidate_line(r, r, r.result, fbs.get(r.pk, []))
            if line:
                lines.append(json.dumps(line, ensure_ascii=False))
        return Response("".join(line + "\n" for line in lines), media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="candidates.jsonl"',
                                 "Cache-Control": "no-store"})

    return router
