-- Every severity code TfL sent in the last 7 days has a weight, under the
-- same description (PLAN 7.1: an unknown code fails rather than defaulting).
-- A renamed code fails too, since its weight may no longer fit.
select distinct
    statuses.mode_name,
    statuses.status_severity,
    statuses.status_severity_description,
    weights.description as seed_description
from {{ ref('stg_line_status') }} as statuses
left join {{ ref('severity_weights') }} as weights
    on statuses.status_severity = weights.severity_code
where
    statuses.dt >= current_date - interval '7' day
    and (
        weights.severity_code is null
        or statuses.status_severity_description <> weights.description
    )
