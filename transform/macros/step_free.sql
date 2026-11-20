{#
  True when a disruption's text reports a step-free access outage: a lift,
  ramp or step-free route that is not available.

  TfL puts the headline "No Step Free Access -" on notices that are not
  about step-free access at all (an escalator fault, a signal failure,
  minor delays, a station closed for staff shortage). The headline is
  therefore removed before matching, and the body has to say that step-free
  access or a lift or ramp is out. Text that only mentions step-free access
  as still available (a footbridge closure, an exit-only entrance at
  Wembley) does not match. The two arguments are column names; null text
  counts as empty.

  Checked by hand against every distinct description in the 9 to 15
  November 2026 disruption polls (docs/methodology.md, "Step-free access").
#}
{% macro is_step_free_outage_text(description, additional_information) %}
    regexp_like(
        regexp_replace(
            lower(coalesce({{ description }}, '') || ' ' || coalesce({{ additional_information }}, '')),
            'no step[- ]free access\s*-\s*',
            ''
        ),
        'no step[- ]free'
        || '|step[- ]free [a-z/ ]{0,60}(is |are )?(not available|unavailable)'
        || '|(faulty|broken) lifts?'
        || '|lifts?\b[^.,;]{0,80}(out of (service|order)|not (in service|working|available)|replacement|maintenance)'
        || '|ramps?\b[^.,;]{0,80}(not|out of) (in )?service'
    )
{%- endmacro %}
