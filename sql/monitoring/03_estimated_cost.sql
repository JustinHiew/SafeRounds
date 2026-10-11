-- Monitoring (2F, D52): estimated model cost per clinic per UTC day, from the token counts on
-- judge_results. Read-only view. This is an OPERATOR query: what a clinic costs is not shown to
-- the clinic (the dashboard and /ui/api/metrics leave it out), and the real total spend against
-- SPEND_LIMIT_USD is in spend_ledger (`python -m safety_eval.service.admin spend`).
--
-- Rates are US dollars per million tokens and MUST match src/safety_eval/service/budget.py PRICES:
-- (input, output, cache read); cache writes cost 1.25 x input. A model not listed is costed at the
-- dearest rate, like budget.rates(). List price only (no Message Batches discount), so it is an
-- upper bound. Results without recorded usage add nothing.

create or replace view monitoring_estimated_cost as
with rates(model, in_rate, out_rate, read_rate) as (
    values ('claude-opus-5-5',   4.00, 20.00, 0.20),
           ('claude-sonnet-5-5', 2.00, 10.00, 0.20),
           ('claude-haiku-5-5',  0.10,  0.50, 0.01)
),
dearest as (
    select max(in_rate) as in_rate, max(out_rate) as out_rate, max(read_rate) as read_rate from rates
)
select
    r.clinic_id,
    (r.created_at at time zone 'UTC')::date as day,
    count(*) filter (where r.input_tokens is not null) as usage_recorded,
    round((sum(
          coalesce(r.input_tokens, 0)       * coalesce(p.in_rate, d.in_rate)
        + coalesce(r.cache_write_tokens, 0) * coalesce(p.in_rate, d.in_rate) * 1.25
        + coalesce(r.cache_read_tokens, 0)  * coalesce(p.read_rate, d.read_rate)
        + coalesce(r.output_tokens, 0)      * coalesce(p.out_rate, d.out_rate)
    ) / 1000000.0)::numeric, 6) as est_cost_usd
from judge_results r
left join rates p on p.model = r.model
cross join dearest d
group by r.clinic_id, (r.created_at at time zone 'UTC')::date;
