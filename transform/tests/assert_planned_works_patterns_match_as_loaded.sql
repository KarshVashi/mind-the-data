-- The seed is loaded by dbt-athena through OpenCSVSerde, which drops
-- backslashes, so a pattern can read fine in the CSV and match nothing in
-- Athena (D69). This runs the loaded patterns on known reasons. Any row
-- returned is an expectation that does not hold.
with cases as (
    select
        reason_text,
        pattern_signal,
        expected_match
    from (
        values
        ('minor delays due to planned engineering work', 'planned_works', true),
        ('unplanned engineering work', 'planned_works', false),
        ('overrunning engineering works', 'overrun', true),
        ('engineering works which are taking longer than expected', 'overrun', true),
        ('works finishes late', 'overrun', true),
        ('planned engineering work is not complete', 'overrun', true),
        ('engineering work will not be completed until 07:00', 'overrun', true),
        ('engineering works taking longer to complete', 'overrun', true),
        ('no service between a and b', 'disruption', true),
        ('severe delays due to a signal failure', 'disruption', true),
        ('good service on the rest of the line', 'disruption', false),
        (
            'tickets will be accepted on london buses via any reasonable route',
            'disruption',
            false
        )
    ) as t (reason_text, pattern_signal, expected_match)
),

results as (
    select
        cases.reason_text,
        cases.pattern_signal,
        cases.expected_match,
        coalesce(
            bool_or(regexp_like(cases.reason_text, patterns.pattern)),
            false
        ) as actual_match
    from cases
    left join {{ ref('planned_works_reasons') }} as patterns
        on cases.pattern_signal = patterns.signal
    group by cases.reason_text, cases.pattern_signal, cases.expected_match
)

select
    reason_text,
    pattern_signal,
    expected_match,
    actual_match
from results
where actual_match <> expected_match
