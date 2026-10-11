-- Monitoring (roadmap 2F, decision D52): one row per clinic per UTC day. Read-only: a view, no tables.
-- Postgres version of src/safety_eval/service/monitoring.py::clinic_daily; same definitions.
-- Run after 005_monitoring.sql (it needs only the tables from 001-002). Numbers only, never text:
-- the view reads verdicts, attempts and latency from judge_results.result and nothing else in it.
--
-- Always filter on clinic_id when querying it for a clinic:
--     select * from monitoring_daily where clinic_id = 'harbourline' and day >= current_date - 28 order by day;
--
-- Rates are over `evaluated` and are null when a denominator is 0.
--   *_fails          results with a 'fail' verdict on that criterion (flag rate per criterion = fails / evaluated)
--   reviewed         results with a reviewer confirm or overturn; overturned = reviewed with at least one overturn
--   overturn_rate    overturned / reviewed, by the day the result was created
--   latency_*        seconds, over results with at least one model attempt (an internal error has none)
--   usage_recorded   results with token counts (NULL counts mean the CLI backend ran, no usage known)

create or replace view monitoring_daily as
select
    r.clinic_id,
    (r.created_at at time zone 'UTC')::date                                             as day,
    count(*)                                                                            as evaluated,
    count(*) filter (where r.flagged)                                                   as flagged,
    round(count(*) filter (where r.flagged)::numeric / count(*), 6)                     as flag_rate,
    count(*) filter (where r.result #>> '{output,criteria,safety,verdict}' = 'fail')     as safety_fails,
    count(*) filter (where r.result #>> '{output,criteria,escalation,verdict}' = 'fail') as escalation_fails,
    count(*) filter (where r.result #>> '{output,criteria,accuracy,verdict}' = 'fail')   as accuracy_fails,
    count(*) filter (where r.result #>> '{output,criteria,scope,verdict}' = 'fail')      as scope_fails,
    count(*) filter (where r.result #>> '{output,criteria,grounding,verdict}' = 'fail')  as grounding_fails,
    count(*) filter (where r.outcome = 'unsure')                                        as unsure,
    round(count(*) filter (where r.outcome = 'unsure')::numeric / count(*), 6)          as unsure_rate,
    count(*) filter (where r.outcome = 'needs_review')                                  as needs_review,
    round(count(*) filter (where r.outcome = 'needs_review')::numeric / count(*), 6)    as needs_review_rate,
    count(*) filter (where rv.reviewed)                                                 as reviewed,
    count(*) filter (where rv.overturned)                                               as overturned,
    round(count(*) filter (where rv.overturned)::numeric
          / nullif(count(*) filter (where rv.reviewed), 0), 6)                          as overturn_rate,
    percentile_cont(0.5) within group (order by (r.result ->> 'latency_s')::double precision)
        filter (where (r.result ->> 'attempts')::int > 0)                               as latency_median_s,
    percentile_cont(0.95) within group (order by (r.result ->> 'latency_s')::double precision)
        filter (where (r.result ->> 'attempts')::int > 0)                               as latency_p95_s,
    count(*) filter (where r.input_tokens is not null)                                  as usage_recorded,
    coalesce(sum(r.input_tokens), 0)                                                    as input_tokens,
    coalesce(sum(r.output_tokens), 0)                                                   as output_tokens,
    coalesce(sum(r.cache_read_tokens), 0)                                               as cache_read_tokens,
    coalesce(sum(r.cache_write_tokens), 0)                                              as cache_write_tokens
from judge_results r
cross join lateral (
    select
        exists (select 1 from feedback f
                where f.result_pk = r.pk and f.clinic_id = r.clinic_id
                  and f.action in ('confirm', 'overturn'))                              as reviewed,
        exists (select 1 from feedback f
                where f.result_pk = r.pk and f.clinic_id = r.clinic_id
                  and f.action = 'overturn')                                            as overturned
) rv
group by r.clinic_id, (r.created_at at time zone 'UTC')::date;
