{#
  Predictions with a missing direction, given one where the boards show it
  (D65). TfL lists many predictions with no direction at some through
  stations (Liverpool Street and King's Cross westbound on the Metropolitan,
  Earl's Court and Barking on the District). The inference keys a board on
  direction and the headways and vehicle passes leave out a null direction,
  so those trains were neither counted nor given an arrival, and the gaps
  around them read long.

  A missing direction is taken, in order, from:
    1. the same train: every other sighting of its vehicle id on the same
       request, stop, line and platform that UTC day lists one direction;
    2. the platform: at least `direction_fill_platform_share` of the
       platform's predictions that day, and at least
       `direction_fill_min_predictions` of them, list one direction.
  Otherwise it stays null and the prediction is left out as before. A
  platform used both ways (a terminus bay) fails both tests.

  Every lookup is grouped by `dt`, so a filter on `dt` gives the same
  result as reading all of history, and a day's directions do not change
  once the day is complete. `direction_source` says which rule applied
  (listed, vehicle, platform, or null).

  predictions: relation shaped like stg_arrivals.
#}
{% macro fill_missing_direction(predictions) %}

with listed as (
    select
        poll_id,
        request_path,
        polled_at,
        prediction_id,
        vehicle_id,
        naptan_id,
        line_id,
        platform_name,
        coalesce(platform_name, '') as platform_key,
        nullif(trim(direction), '') as listed_direction,
        case
            when
                vehicle_id in (
{%- for placeholder in var('placeholder_vehicle_ids') %}
                        '{{ placeholder }}'{{ ',' if not loop.last }}
    {%- endfor %}
                )
                or trim(vehicle_id) = ''
                then null
            else vehicle_id
        end as known_vehicle_id,
        time_to_station,
        expected_arrival,
        dt
    from {{ predictions }}
),

vehicle_directions as (
    -- Rule 1: the train's own direction, when its sightings agree.
    select
        request_path,
        naptan_id,
        line_id,
        platform_key,
        known_vehicle_id,
        dt,
        min(listed_direction) as direction
    from listed
    where known_vehicle_id is not null and listed_direction is not null
    group by request_path, naptan_id, line_id, platform_key, known_vehicle_id, dt
    having count(distinct listed_direction) = 1
),

platform_counts as (
    select
        request_path,
        naptan_id,
        line_id,
        platform_key,
        dt,
        listed_direction,
        count(*) as predictions
    from listed
    where listed_direction is not null
    group by request_path, naptan_id, line_id, platform_key, dt, listed_direction
),

platform_directions as (
    -- Rule 2: the platform's direction, when it carries one direction.
    select
        request_path,
        naptan_id,
        line_id,
        platform_key,
        dt,
        max_by(listed_direction, predictions) as direction
    from platform_counts
    group by request_path, naptan_id, line_id, platform_key, dt
    having
        max(predictions) >= {{ var('direction_fill_platform_share') }} * sum(predictions)
        and sum(predictions) >= {{ var('direction_fill_min_predictions') }}
)

select
    listed.poll_id,
    listed.request_path,
    listed.polled_at,
    listed.prediction_id,
    listed.vehicle_id,
    listed.naptan_id,
    listed.line_id,
    listed.platform_name,
    coalesce(
        listed.listed_direction, vehicle_directions.direction, platform_directions.direction
    ) as direction,
    case
        when listed.listed_direction is not null then 'listed'
        when vehicle_directions.direction is not null then 'vehicle'
        when platform_directions.direction is not null then 'platform'
    end as direction_source,
    listed.time_to_station,
    listed.expected_arrival,
    listed.dt
from listed
left join vehicle_directions
    on
        listed.listed_direction is null
        and listed.request_path = vehicle_directions.request_path
        and listed.naptan_id = vehicle_directions.naptan_id
        and listed.line_id = vehicle_directions.line_id
        and listed.platform_key = vehicle_directions.platform_key
        and listed.known_vehicle_id = vehicle_directions.known_vehicle_id
        and listed.dt = vehicle_directions.dt
left join platform_directions
    on
        listed.listed_direction is null
        and listed.request_path = platform_directions.request_path
        and listed.naptan_id = platform_directions.naptan_id
        and listed.line_id = platform_directions.line_id
        and listed.platform_key = platform_directions.platform_key
        and listed.dt = platform_directions.dt
{% endmacro %}
