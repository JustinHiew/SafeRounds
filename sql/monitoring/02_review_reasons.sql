-- Monitoring (2F, D52): why results went to needs_review, as fixed codes, per clinic per UTC day.
-- Read-only view. Same mapping as monitoring.py::reason_code, in the same order (first match wins).
-- The reason text itself is never selected: only the code leaves the case expression.
-- A needs_review result with no reasons counts once as 'other'; a result counts once per code.
--
--     select * from monitoring_review_reasons where clinic_id = 'harbourline' and day >= current_date - 28;

create or replace view monitoring_review_reasons as
select
    t.clinic_id,
    t.day,
    t.code,
    count(*) as results
from (
    select distinct
        r.clinic_id,
        r.pk,
        (r.created_at at time zone 'UTC')::date as day,
        coalesce(
            case
                when x.reason ~ 'quote not found'     then 'quote_not_found'
                when x.reason ~ 'without evidence'    then 'missing_evidence'
                when x.reason ~ 'spend limit'         then 'spend_limit'
                when x.reason ~ 'call failed'         then 'call_failed'
                when x.reason ~ 'malformed output'    then 'malformed_output'
                when x.reason ~ 'inconsistent output' then 'inconsistent_output'
                when x.reason ~ 'internal error'      then 'internal_error'
                else 'other'
            end, 'other') as code
    from judge_results r
    left join lateral jsonb_array_elements_text(r.result -> 'reasons') as x(reason) on true
    where r.outcome = 'needs_review'
) t
group by t.clinic_id, t.day, t.code;
