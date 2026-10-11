"""Monitoring and alerts (roadmap 2F, decision D52).

Daily metrics per clinic, alert rules over them, and de-duplicated notification.

Rules that hold here, as everywhere in the service:
- Numbers, codes and ids only. Nothing in this module reads conversation text, evidence, analysis
  or notes: the one JSON column it reads (judge_results.result) is used for verdicts, `reasons`
  (mapped to fixed codes, the text itself is dropped), `attempts` and `latency_s`.
- Every clinic query filters on clinic_id.
- Spend is global and the operator's business: it appears in `check` and in the webhook, never in
  the per-clinic metrics or the dashboard.
- Fail closed: a webhook that cannot be reached is not recorded as sent, so the next run retries.

The same metrics as Postgres queries are in sql/monitoring/. This module computes them in Python
over one clinic's rows for the window, so it behaves the same on SQLite and Postgres.
"""

import json
import logging
import math
import re
import statistics
import urllib.request
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse

import sqlalchemy as sa

from safety_eval.schemas import CRITERIA

from .budget import usage_cost
from .db import alert_state, as_utc, clinics, feedback, judge_results, utcnow

log = logging.getLogger("safety_eval.service")

BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 7          # days with data needed before a spike can be called
OVERTURN_WINDOW_DAYS = 7
GLOBAL = "global"

# needs_review reasons, as fixed codes. The judge's reason text can quote a model error message,
# so only the code ever leaves this module.
REASON_CODES = ("quote_not_found", "missing_evidence", "call_failed", "spend_limit",
                "malformed_output", "inconsistent_output", "internal_error", "other")
_REASON_PATTERNS = (
    ("quote_not_found", re.compile(r"quote not found")),
    ("missing_evidence", re.compile(r"without evidence")),
    ("spend_limit", re.compile(r"spend limit")),
    ("call_failed", re.compile(r"call failed")),
    ("malformed_output", re.compile(r"malformed output")),
    ("inconsistent_output", re.compile(r"inconsistent output")),
    ("internal_error", re.compile(r"internal error")),
)


def reason_code(reason):
    for code, pattern in _REASON_PATTERNS:
        if pattern.search(str(reason)):
            return code
    return "other"


# ------------------------------------------------------------------ maths

def percentile(values, q):
    """Linear interpolation between closest ranks (the same as Postgres percentile_cont)."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _rate(n, total):
    return round(n / total, 6) if total else None


def day_start(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


# ------------------------------------------------------------------ daily metrics

def _empty_day(day):
    return {"date": day.isoformat(), "evaluated": 0, "flagged": 0, "flag_rate": None,
            "criterion_fails": {c: 0 for c in CRITERIA}, "criterion_flag_rate": {c: None for c in CRITERIA},
            "unsure": 0, "unsure_rate": None, "needs_review": 0, "needs_review_rate": None,
            "review_reasons": {}, "reviewed": 0, "overturned": 0, "overturn_rate": None,
            "latency_median_s": None, "latency_p95_s": None, "usage_recorded": 0,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "est_cost_usd": 0.0}


def clinic_daily(engine, clinic_id, days=BASELINE_DAYS, today=None):
    """One dict per UTC day, oldest first, ending today, for one clinic.

    Definitions (docs/monitoring.md):
    - rates are over `evaluated` (judge results created that day); None when there were none;
    - flag rate per criterion = results with a fail verdict on it / evaluated;
    - reviewed = results with a confirm or overturn from a reviewer; overturned = reviewed results
      with at least one overturn; the overturn rate is overturned / reviewed, and is attributed to
      the day the result was created, not the day it was reviewed;
    - latency covers results with at least one model attempt (an internal error has none);
    - est_cost_usd is usage_cost at list price per result's model (never the batch discount), so it
      is an upper bound; results with no recorded usage add nothing.
    """
    today = today or utcnow().date()
    start = today - timedelta(days=days - 1)
    since, until = day_start(start), day_start(today + timedelta(days=1))
    scope = sa.and_(judge_results.c.clinic_id == clinic_id, judge_results.c.created_at >= since,
                    judge_results.c.created_at < until)
    with engine.connect() as conn:
        rows = conn.execute(sa.select(
            judge_results.c.pk, judge_results.c.created_at, judge_results.c.outcome,
            judge_results.c.flagged, judge_results.c.model, judge_results.c.result,
            judge_results.c.input_tokens, judge_results.c.output_tokens,
            judge_results.c.cache_read_tokens, judge_results.c.cache_write_tokens).where(scope)).all()
        marks = conn.execute(sa.select(
            feedback.c.result_pk, feedback.c.action).where(
            feedback.c.clinic_id == clinic_id, feedback.c.action.in_(("confirm", "overturn")),
            feedback.c.result_pk.in_(sa.select(judge_results.c.pk).where(scope)))).all()
    reviewed, overturned = set(), set()
    for pk, action in marks:
        reviewed.add(pk)
        if action == "overturn":
            overturned.add(pk)
    out = {(start + timedelta(days=i)).isoformat(): _empty_day(start + timedelta(days=i)) for i in range(days)}
    latencies = {k: [] for k in out}
    for r in rows:
        key = as_utc(r.created_at).date().isoformat()
        d = out.get(key)
        if d is None:
            continue
        result = r.result if isinstance(r.result, dict) else json.loads(r.result)
        d["evaluated"] += 1
        d["flagged"] += bool(r.flagged)
        d["unsure"] += r.outcome == "unsure"
        if r.outcome == "needs_review":
            d["needs_review"] += 1
            for code in {reason_code(x) for x in result.get("reasons") or []} or {"other"}:
                d["review_reasons"][code] = d["review_reasons"].get(code, 0) + 1
        for crit, j in (((result.get("output") or {}).get("criteria")) or {}).items():
            if crit in d["criterion_fails"] and isinstance(j, dict) and j.get("verdict") == "fail":
                d["criterion_fails"][crit] += 1
        if r.pk in reviewed:
            d["reviewed"] += 1
            d["overturned"] += r.pk in overturned
        if (result.get("attempts") or 0) > 0 and isinstance(result.get("latency_s"), (int, float)):
            latencies[key].append(float(result["latency_s"]))
        counts = (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens)
        if any(c is not None for c in counts):
            d["usage_recorded"] += 1
            i, o, cr, cw = (c or 0 for c in counts)
            d["input_tokens"] += i
            d["output_tokens"] += o
            d["cache_read_tokens"] += cr
            d["cache_write_tokens"] += cw
            d["est_cost_usd"] += usage_cost(r.model, {
                "input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw})
    for key, d in out.items():
        n = d["evaluated"]
        d["flag_rate"] = _rate(d["flagged"], n)
        d["unsure_rate"] = _rate(d["unsure"], n)
        d["needs_review_rate"] = _rate(d["needs_review"], n)
        d["criterion_flag_rate"] = {c: _rate(d["criterion_fails"][c], n) for c in CRITERIA}
        d["overturn_rate"] = _rate(d["overturned"], d["reviewed"])
        lat = latencies[key]
        d["latency_median_s"] = round(statistics.median(lat), 3) if lat else None
        d["latency_p95_s"] = round(percentile(lat, 0.95), 3) if lat else None
        d["est_cost_usd"] = round(d["est_cost_usd"], 6)
        d["review_reasons"] = dict(sorted(d["review_reasons"].items()))
    return list(out.values())


# ------------------------------------------------------------------ alerts

def _alert(scope, code, metric, **numbers):
    """An alert is a dict of codes and numbers. `scope` is a clinic id or "global"."""
    return {"scope": scope, "code": code, "metric": metric, **numbers}


def _spike(scope, code, metric, today_rate, baseline, n_today, settings, date):
    """A spike alert when today's rate is further than sigma standard deviations from the
    trailing mean AND at least `alert_min_shift` away in absolute terms (so a quiet history with a
    near-zero deviation cannot fire on noise)."""
    if today_rate is None or len(baseline) < MIN_BASELINE_DAYS:
        return None
    mean, sd = statistics.fmean(baseline), statistics.stdev(baseline)
    shift = today_rate - mean
    if abs(shift) > settings.alert_sigma * sd and abs(shift) >= settings.alert_min_shift:
        return _alert(scope, code, metric, direction="high" if shift > 0 else "low", date=date,
                      value=round(today_rate, 4), baseline_mean=round(mean, 4),
                      baseline_sd=round(sd, 4), sigma=settings.alert_sigma, n=n_today)
    return None


def clinic_alerts(engine, clinic_id, settings, today=None):
    """Active alerts for one clinic, from its own numbers. Today is the current (partial) UTC day."""
    today = today or utcnow().date()
    daily = clinic_daily(engine, clinic_id, BASELINE_DAYS + 1, today)
    now, history = daily[-1], daily[:-1]
    alerts, date = [], now["date"]
    if now["evaluated"] >= settings.alert_min_volume:
        series = [("flag_rate_shift", "flag_rate", "flag_rate")]
        series += [(f"criterion_flag_rate_shift:{c}", f"flag_rate:{c}", c) for c in CRITERIA]
        series += [("unsure_rate_shift", "unsure_rate", "unsure_rate"),
                   ("needs_review_rate_shift", "needs_review_rate", "needs_review_rate")]
        for code, metric, key in series:
            if key in CRITERIA:
                cur, base = now["criterion_flag_rate"][key], [h["criterion_flag_rate"][key] for h in history]
            else:
                cur, base = now[key], [h[key] for h in history]
            a = _spike(clinic_id, code, metric, cur, [b for b in base if b is not None],
                       now["evaluated"], settings, date)
            if a:
                alerts.append(a)
        if now["needs_review_rate"] > settings.alert_needs_review_ceiling:
            alerts.append(_alert(clinic_id, "needs_review_rate_high", "needs_review_rate", date=date,
                                 value=round(now["needs_review_rate"], 4),
                                 ceiling=settings.alert_needs_review_ceiling, n=now["evaluated"]))
    window = daily[-OVERTURN_WINDOW_DAYS:]
    reviewed, overturned = sum(d["reviewed"] for d in window), sum(d["overturned"] for d in window)
    if reviewed >= settings.alert_min_reviewed and overturned / reviewed > settings.alert_overturn_ceiling:
        alerts.append(_alert(clinic_id, "overturn_rate_high", "overturn_rate", date=date,
                             value=round(overturned / reviewed, 4), ceiling=settings.alert_overturn_ceiling,
                             n=reviewed, window_days=OVERTURN_WINDOW_DAYS))
    return alerts


def spend_alerts(engine, settings):
    """Operator-only: spend at or past alert_spend_fraction of SPEND_LIMIT_USD (settled plus open reservations)."""
    if not settings.spend_limit_usd:
        return []
    from .budget import Budget
    s = Budget(engine, settings.spend_limit_usd).summary()
    used = s["settled_usd"] + s["reserved_usd"]
    fraction = used / settings.spend_limit_usd
    if fraction >= settings.alert_spend_fraction:
        return [_alert(GLOBAL, "spend_near_limit", "spend", value=round(fraction, 4),
                       threshold=settings.alert_spend_fraction, spent_usd=round(used, 4),
                       limit_usd=settings.spend_limit_usd)]
    return []


def all_clinic_ids(engine):
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.select(clinics.c.id).order_by(clinics.c.id))]


def compute_alerts(engine, settings, today=None):
    """Every active alert: each clinic's own, then the global spend alert."""
    alerts = []
    for cid in all_clinic_ids(engine):
        alerts += clinic_alerts(engine, cid, settings, today)
    return alerts + spend_alerts(engine, settings)


# ------------------------------------------------------------------ notification

def payload_for(alerts, now=None):
    """The webhook body: alert codes, clinic ids and numbers. Built field by field from a fixed
    allow-list so nothing else can ride along."""
    keys = ("scope", "code", "metric", "direction", "date", "value", "baseline_mean", "baseline_sd", "sigma",
            "ceiling", "threshold", "n", "window_days", "spent_usd", "limit_usd")
    return {"generated_at": (now or utcnow()).isoformat(),
            "alerts": [{k: a[k] for k in keys if k in a} for a in alerts]}


def post_json(url, payload, timeout=10):
    """The real sender. http(s) only; any failure raises, and the caller then records nothing."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("the alert webhook URL must be http or https")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (scheme checked above)
        if not 200 <= resp.status < 300:
            raise OSError(f"webhook answered {resp.status}")


def select_unsent(engine, alerts, cooldown_hours, now=None):
    """Alerts not sent within the cooldown. Also forgets alerts that are no longer active, so one
    that clears and later returns fires again."""
    now = now or utcnow()
    active = {(a["scope"], a["code"]) for a in alerts}
    fresh = []
    with engine.begin() as conn:
        rows = conn.execute(sa.select(alert_state)).all()
        for r in rows:
            if (r.scope, r.code) not in active:
                conn.execute(alert_state.delete().where(alert_state.c.scope == r.scope,
                                                        alert_state.c.code == r.code))
        sent = {(r.scope, r.code): as_utc(r.last_sent_at) for r in rows}
    for a in alerts:
        last = sent.get((a["scope"], a["code"]))
        if last is None or now - last >= timedelta(hours=cooldown_hours):
            fresh.append(a)
    return fresh


def record_sent(engine, alerts, now=None):
    now = now or utcnow()
    with engine.begin() as conn:
        for a in alerts:
            where = sa.and_(alert_state.c.scope == a["scope"], alert_state.c.code == a["code"])
            values = {"last_sent_at": now, "last_value": a.get("value")}
            if conn.execute(alert_state.update().where(where).values(**values)).rowcount == 0:
                conn.execute(alert_state.insert().values(scope=a["scope"], code=a["code"], **values))


def notify(engine, settings, alerts, sender=post_json, now=None):
    """Send the alerts that are new (or past the cooldown) in one webhook call. Returns the list
    sent. Nothing is sent without a webhook URL; a failed send records nothing, so the next run
    retries (fail closed: an alert is never silently marked as delivered)."""
    if not settings.alert_webhook_url:
        raise ValueError("ALERT_WEBHOOK_URL is not set")
    fresh = select_unsent(engine, alerts, settings.alert_cooldown_hours, now)
    if not fresh:
        return []
    sender(settings.alert_webhook_url, payload_for(fresh, now))
    record_sent(engine, fresh, now)
    return fresh
