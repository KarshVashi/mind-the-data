-- The patterns as loaded into Athena name the cause they should (D74).
-- OpenCSVSerde drops a backslash and may mangle a quote or a bar, so the
-- seed table is run against sample phrases, not just read back. A sample is
-- wrong, and returned, when the first-starting cause is not the expected one.
-- The phrases are TfL wordings seen in W46, trimmed, with the first named
-- cause the rule should give.
with samples as (
    select * from (
        values
        ('central line: minor delays due to train cancellations.', 'Train cancellations'),
        ('while we fix a signal failure at kenton.', 'Signal failures'),
        ('due to an earlier signalling problem at rickmansworth', 'Signal failures'),
        ('while we fix a points failure at barking.', 'Points failures'),
        ('severe delays due to an earlier faulty train at westminster.', 'Faulty trains'),
        ('severe delays due to a track fault at brixton.', 'Track faults'),
        ('minor delays due to an earlier customer incident.', 'Customer incidents and illness'),
        (
            'severe delays due to an earlier animal on the track.',
            'People, animals or objects on the track'
        ),
        ('due to the unavailability of depot staff.', 'Staff unavailable'),
        ('minor delays due to an earlier fire near the track.', 'Fire alerts and fires'),
        ('minor delays while we fix an electrical fault.', 'Power and electrical faults'),
        (
            'minor delays due to a road traffic collision at west croydon.',
            'Road and bridge incidents'
        ),
        ('no service due to planned engineering works.', 'Engineering works and overruns'),
        ('minor delays due to flooding at the station.', 'Flooding and weather'),
        (
            'no service while we respond to a security alert.',
            'Emergency services and security alerts'
        ),
        (
            'a reduced service will operate between gospel oak and woodgrange park.',
            'Planned service changes'
        ),
        ('minor delays while we fix faulty platform screen doors.', 'Platform screen door faults'),
        ('minor delays while we conduct a track inspection at queensbury.', 'Track inspections'),
        (
            'severe delays between sydenham and west croydon due to late running of trains.',
            'Late running'
        ),
        (
            'minor delays due to congestion caused by disruption on the mildmay line.',
            'Congestion from other lines'
        ),
        -- First named wins over the lower priority number.
        (
            'severe delays while we fix a signal failure and due to train cancellations.',
            'Signal failures'
        ),
        ('minor delays due to an earlier faulty train and train cancellations.', 'Faulty trains'),
        -- A station name is not a cause.
        ('minor delays between battersea power station and kennington.', null)
    ) as t (reason_text, expected_cause)
),

matches as (
    select
        samples.reason_text,
        samples.expected_cause,
        patterns.cause,
        row_number() over (
            partition by samples.reason_text
            order by
                regexp_position(concat(' ', samples.reason_text, ' '), patterns.pattern),
                patterns.priority
        ) as cause_rank
    from samples
    inner join {{ ref('disruption_cause_patterns') }} as patterns
        on regexp_position(concat(' ', samples.reason_text, ' '), patterns.pattern) > 0
),

first_cause as (
    select
        reason_text,
        cause
    from matches
    where cause_rank = 1
)

select
    samples.reason_text,
    samples.expected_cause,
    first_cause.cause as found_cause
from samples
left join first_cause
    on samples.reason_text = first_cause.reason_text
where samples.expected_cause is distinct from first_cause.cause
