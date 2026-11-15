{{ config(materialized='table') }}

-- Divisions by calendar month (PLAN 6.2, M6; DECISIONS.md D57), one row per
-- line and month from the first division month (rpt_division_seeding).
--
-- Each line's month is its Disruption Index over the month's London dates,
-- recomputed from summed minutes (D19), with the weekly table's rules: a
-- line that ran under `min_open_share_to_rank` of its scheduled minutes, or
-- has no scored minutes, is not ranked (D20, D22). Within a division,
-- lowest index first, ties to the higher Good Service %, still-equal lines
-- sharing a rank.
--
-- At the end of each month the bottom `division_moves` ranked lines of
-- Division One swap with the top `division_moves` ranked lines of Division
-- Two (ties split by line id). Unranked lines neither go up nor down. When
-- a division has fewer ranked lines than places, fewer lines move, so both
-- divisions keep their size.
--
-- Membership in a month depends on the month before, so the months are
-- chained at compile time, from the month of `raw_start_date` to
-- `division_months_through` (default: the current month). Months before
-- the first division month are empty; before seeding the model is empty.

{%- set start = modules.datetime.date.fromisoformat(var('raw_start_date')) %}
{%- set through_var = var('division_months_through') %}
{%- if through_var %}
    {%- set through = modules.datetime.date.fromisoformat(through_var) %}
{%- else %}
    {#- The London date, as the export uses: a runner's UTC date lags it
        for an hour after midnight in summer. #}
    {%- set through = modules.datetime.datetime.now(
        modules.pytz.timezone('Europe/London')
    ).date() %}
{%- endif %}
{%- set months = [] %}
{%- set ns = namespace(year=start.year, month=start.month) %}
{%- for _ in range(1200) %}
    {%- if (ns.year, ns.month) <= (through.year, through.month) %}
        {%- do months.append('%04d-%02d-01' % (ns.year, ns.month)) %}
        {%- set ns.month = ns.month + 1 %}
        {%- if ns.month > 12 %}
            {%- set ns.month = 1 %}
            {%- set ns.year = ns.year + 1 %}
        {%- endif %}
    {%- endif %}
{%- endfor %}

with daily as (
    select
        line_id,
        scheduled_minutes,
        observed_minutes,
        engineering_works_minutes,
        scored_minutes,
        good_service_minutes,
        weighted_disruption_minutes,
        time_weighted_scored_minutes,
        date_trunc('month', london_date) as month_start
    from {{ ref('fct_line_daily') }}
),

monthly as (
    select
        line_id,
        month_start,
        sum(scheduled_minutes) as scheduled_minutes,
        sum(observed_minutes) as observed_minutes,
        sum(engineering_works_minutes) as engineering_works_minutes,
        sum(scored_minutes) as scored_minutes,
        sum(good_service_minutes) as good_service_minutes,
        sum(weighted_disruption_minutes) as weighted_disruption_minutes,
        sum(time_weighted_scored_minutes) as time_weighted_scored_minutes
    from daily
    group by line_id, month_start
),

monthly_scored as (
    select
        *,
        {{ disruption_index('weighted_disruption_minutes', 'time_weighted_scored_minutes') }}
            as disruption_index,
        {{ percent_of('good_service_minutes', 'scored_minutes') }} as good_service_pct,
        if(scheduled_minutes > 0, 1e0 * observed_minutes / scheduled_minutes) as completeness,
        if(scheduled_minutes > 0, 1e0 * engineering_works_minutes / scheduled_minutes)
            as engineering_works_share
    from monthly
),

seeding as (
    select
        line_id,
        division,
        first_division_month
    from {{ ref('rpt_division_seeding') }}
),

{% for month in months %}
    {%- set i = loop.index0 %}
    members_{{ i }} as (
        select
            line_id,
            division,
            'seeded' as joined_by
        from seeding
        where first_division_month = date '{{ month }}'
        {%- if not loop.first %}
        union all
        select
            line_id,
            case movement when 'promoted' then 1 when 'relegated' then 2 else division end
                as division,
            if(movement = 'stays', 'stayed', movement) as joined_by
        from standings_{{ i - 1 }}
    {%- endif %}
    ),

    scored_{{ i }} as (
        select
            members_{{ i }}.line_id,
            members_{{ i }}.division,
            members_{{ i }}.joined_by,
            date '{{ month }}' as month_start,
            monthly_scored.disruption_index,
            monthly_scored.good_service_pct,
            coalesce(monthly_scored.scheduled_minutes, 0) as scheduled_minutes,
            coalesce(monthly_scored.observed_minutes, 0) as observed_minutes,
            coalesce(monthly_scored.engineering_works_minutes, 0) as engineering_works_minutes,
            coalesce(monthly_scored.scored_minutes, 0) as scored_minutes,
            coalesce(monthly_scored.completeness, 0) as completeness,
            coalesce(monthly_scored.engineering_works_share, 0) as engineering_works_share,
            case
                when
                    monthly_scored.engineering_works_share > 1 - {{ var('min_open_share_to_rank') }}
                    then 'engineering_works'
                when monthly_scored.disruption_index is null then 'no_scored_minutes'
            end as unranked_reason
        from members_{{ i }}
        left join monthly_scored
            on
                members_{{ i }}.line_id = monthly_scored.line_id
                and monthly_scored.month_start = date '{{ month }}'
    ),

    positioned_{{ i }} as (
        select
            *,
            if(
                unranked_reason is null,
                rank() over (
                    partition by division, unranked_reason is null
                    order by disruption_index asc, good_service_pct desc nulls last
                )
            ) as division_rank,
            if(
                unranked_reason is null,
                row_number() over (
                    partition by division, unranked_reason is null
                    order by disruption_index asc, good_service_pct desc nulls last, line_id asc
                )
            ) as ranked_position,
            count_if(division = 1 and unranked_reason is null) over () as ranked_in_division_one,
            -- Places that change hands: never more than either division has ranked lines.
            least(
                {{ var('division_moves') }},
                count_if(division = 1 and unranked_reason is null) over (),
                count_if(division = 2 and unranked_reason is null) over ()
            ) as moves
        from scored_{{ i }}
    ),

    standings_{{ i }} as (
        select
            *,
            case
                when
                    division = 1 and ranked_position > ranked_in_division_one - moves
                    then 'relegated'
                when division = 2 and ranked_position <= moves then 'promoted'
                else 'stays'
            end as movement
        from positioned_{{ i }}
    ),

{% endfor %}
all_months as (
    {%- for month in months %}
        select * from standings_{{ loop.index0 }}
        {%- if not loop.last %}
            union all
        {%- endif %}
    {%- endfor %}
)

select
    line_id,
    month_start,
    date_add('day', -1, date_add('month', 1, month_start)) as month_end,
    division,
    joined_by,
    scheduled_minutes,
    observed_minutes,
    engineering_works_minutes,
    scored_minutes,
    disruption_index,
    good_service_pct,
    completeness,
    completeness < {{ var('provisional_completeness') }} as is_provisional,
    engineering_works_share,
    unranked_reason,
    division_rank,
    movement,
    case movement when 'promoted' then 1 when 'relegated' then 2 else division end
        as next_division,
    -- A month is complete once its last London date has passed; until then
    -- `movement` is where the line stands, not a result.
    date_add('month', 1, month_start)
    <= cast(
        {{ london_local("cast(current_timestamp at time zone 'UTC' as timestamp(6))") }}
        as date
    ) as is_month_complete,
    {{ row_key(['line_id', 'month_start']) }} as line_month_key
from all_months
