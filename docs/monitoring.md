# Monitoring and alerts (product phase 2F)

Daily numbers per clinic, alerts when they move, and a dashboard page for the clinic's admins.
Code: `src/safety_eval/service/monitoring.py` (metrics, rules, notification), `monitor.py` (CLI),
`static/dashboard.html`, the two `/ui` routes in `ui.py`. Postgres equivalents: `sql/monitoring/`.

Everything here holds the service's rules: **numbers, codes and ids only** (no conversation text,
record values, model output, notes or reviewer names reach a metric, log, alert or webhook), every
clinic query filters on `clinic_id`, and a failed notification is never recorded as sent.

## Metrics (per clinic, per UTC day)

| Metric | Definition |
|---|---|
| `evaluated` | judge results created that day |
| `flag_rate` | results the judge flagged / evaluated |
| `criterion_flag_rate` | results with a `fail` verdict on that criterion / evaluated (five criteria) |
| `unsure_rate` | outcome `unsure` / evaluated |
| `needs_review_rate` | outcome `needs_review` / evaluated |
| `review_reasons` | per needs-review result, which of these codes apply (a result counts once per code): `quote_not_found`, `missing_evidence`, `call_failed`, `spend_limit`, `malformed_output`, `inconsistent_output`, `internal_error`, `other` |
| `reviewed`, `overturned`, `overturn_rate` | reviewed = a reviewer confirmed or overturned (a comment does not count); overturned = reviewed with at least one overturn; rate = overturned / reviewed, by the day the **result** was created, so a day's rate can change as reviews arrive |
| `latency_median_s`, `latency_p95_s` | seconds across the model calls behind one conversation, over results with at least one model attempt (an internal error has none) |
| tokens | input, output, cache read, cache write, summed from the `judge_results` columns |
| `est_cost_usd` | `budget.usage_cost` per result at list price (never the batch discount), so an upper bound; results with no recorded usage add nothing |

Rates are `null` when the denominator is 0. Reason codes come from the judge's reason strings by
pattern (`monitoring.reason_code`); the text itself is dropped. Unknown text becomes `other`.

Computed in Python over one clinic's rows for the window, with SQLAlchemy Core, so it behaves the
same on SQLite and Postgres. That is fine at pilot volume; if a clinic reaches hundreds of
thousands of results a month, switch the heavy path to the views below.

### Postgres queries

`sql/monitoring/*.sql` define read-only views (`create or replace view`, no tables), run after
`005_monitoring.sql`:

| File | View |
|---|---|
| `01_daily_metrics.sql` | `monitoring_daily`: counts, rates, reviewed/overturned, latency percentiles, tokens |
| `02_review_reasons.sql` | `monitoring_review_reasons`: results per clinic, day and reason code |
| `03_estimated_cost.sql` | `monitoring_estimated_cost`: operator-only estimate; rates must match `budget.PRICES` (a test checks) |

Always add `where clinic_id = ...` when querying them for a clinic. **These have been written but
not run against a real Postgres** (none was available when they were written); a test checks that
they are views only, contain no writes, never select text columns and keep the rates in step.
Please run them once against Supabase and compare with `monitor report`.

## Alerts

`python -m safety_eval.service.monitor check` prints the active alerts and sends nothing.

| Code | Fires when | Default |
|---|---|---|
| `flag_rate_shift`, `criterion_flag_rate_shift:<criterion>`, `unsure_rate_shift`, `needs_review_rate_shift` | today's rate is more than `ALERT_SIGMA` standard deviations from the trailing 28-day mean (days with data, at least 7 of them), **and** at least `ALERT_MIN_SHIFT` away in absolute rate, **and** there are at least `ALERT_MIN_VOLUME` evaluations today. High or low (a drop can mean the judge broke) | 2.0, 0.10, 20 |
| `needs_review_rate_high` | needs-review rate today above `ALERT_NEEDS_REVIEW_CEILING`, with at least `ALERT_MIN_VOLUME` evaluations | 0.15 |
| `overturn_rate_high` | over the last 7 days at least `ALERT_MIN_REVIEWED` results reviewed and the overturn rate above `ALERT_OVERTURN_CEILING` | 10, 0.30 |
| `spend_near_limit` | **global, operator-only**: settled plus open spend at or over `ALERT_SPEND_FRACTION` of `SPEND_LIMIT_USD` | 0.8 |

The absolute floor matters: on a very steady history the standard deviation is near zero and any
change would be "many sigmas". "Today" is the current, partial UTC day, so early in the day the
volume rule keeps it quiet.

### Notify

```bash
export ALERT_WEBHOOK_URL=https://hooks.example.com/...     # optional; empty = never send
python -m safety_eval.service.monitor check --notify       # run from cron, e.g. every 30 minutes
python -m safety_eval.service.monitor check --json         # the payload shape, nothing sent
python -m safety_eval.service.monitor report --clinic harbourline --days 14   # daily metrics incl. cost, for the operator
```

`--notify` makes one JSON POST (http or https only, 10 second timeout) with the alerts that are new:

```json
{"generated_at": "2026-10-11T03:00:00+00:00",
 "alerts": [{"scope": "harbourline", "code": "flag_rate_shift", "metric": "flag_rate", "direction": "high",
             "date": "2026-10-11", "value": 0.5, "baseline_mean": 0.23, "baseline_sd": 0.03, "sigma": 2.0, "n": 30}]}
```

The body is built field by field from a fixed allow-list of codes, clinic ids and numbers. A test
seeds a distinctive phrase into every text-bearing column and checks it never appears.

**Dedupe.** The table `alert_state` (`sql/005_monitoring.sql`; SQLite creates it itself) records
when each `(scope, code)` was last sent. An alert is not re-sent within `ALERT_COOLDOWN_HOURS`
(24); an alert that clears is forgotten, so it fires at once if it comes back. If the POST fails,
nothing is recorded and the command exits non-zero, so the next run retries. Without `--notify` the
state is not touched.

| Variable | Default |
|---|---|
| `ALERT_WEBHOOK_URL` | empty |
| `ALERT_SIGMA` | `2.0` |
| `ALERT_MIN_VOLUME` | `20` |
| `ALERT_MIN_SHIFT` | `0.10` |
| `ALERT_NEEDS_REVIEW_CEILING` | `0.15` |
| `ALERT_OVERTURN_CEILING` | `0.30` |
| `ALERT_MIN_REVIEWED` | `10` |
| `ALERT_SPEND_FRACTION` | `0.8` |
| `ALERT_COOLDOWN_HOURS` | `24` |

Alerts go to the **operator's** webhook. Clinics are not notified directly; a clinic admin sees its
own alerts on the dashboard. Clinic ids in the payload are ids, not names.

## Dashboard

`/ui/dashboard` (a link appears in the review queue header for admins) shows, for the signed-in
admin's clinic only: current alerts and the rules in force, today's tiles, a 28-day trend table
with a sparkline per metric (inline SVG drawn by the page's script, scaled to its own range, a
gap for days with no data), why results went to review, and a daily table. Reviewers get 403; not
signed in redirects to the login page. The data comes from `GET /ui/api/metrics?days=28` (1 to 90
days, same session auth, admin role).

The page follows the other pages' rules: one inline script and one inline style pinned by hash in
the CSP, no external requests, no inline `style=` or `on*=` attributes, text set with
`textContent`, dark mode through the same colour tokens, and it works at 390 px (tables scroll
inside their card, not the page). `tools/ui_check/check.py` has real-browser checks for it (run by
hand, not in the test suite).

**Spend and cost are not on the dashboard or in `/ui/api/metrics`**: the spend limit is the
operator's API credit across all clinics, and what one clinic costs the operator is the operator's
business. Both are in `monitor check` / `monitor report` and `admin spend`.
