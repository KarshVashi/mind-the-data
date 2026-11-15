{{ config(severity='warn') }}

-- An `announced` action in seed_industrial_action must become `held` or
-- `called_off` once it has passed (D60): a strike called off but left as
-- announced would silently drop real days from the strikes-excluded league.
-- Three days' grace after the last day, then this warns. It is a warning,
-- not an error, so a missed hand update never stops the nightly build; the
-- daily ops report lists the same rows as a problem (scripts/ops_report.py,
-- runbook "Industrial action seed").

select
    action_id,
    end_london_date
from {{ ref('seed_industrial_action') }}
where
    status = 'announced'
    and end_london_date
    < date_add(
        'day',
        -3,
        cast(
            {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
            as date
        )
    )
