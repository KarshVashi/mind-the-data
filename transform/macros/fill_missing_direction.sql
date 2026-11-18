{#
  Predictions with a missing direction, given one where the boards show it
  (D65). TfL lists many predictions with no direction at some through
  stations: on the Metropolitan, about half of those at Liverpool Street
  and King's Cross westbound. The inference keys a board on direction, and
  the headways and vehicle passes leave out a null direction, so those
  trains were neither counted nor given an arrival, and the gaps around
  them read long.

  A missing direction is taken, in order, from:
    1. the same train: every other sighting of its vehicle id on the same
       request, stop, line and platform that UTC day lists one direction.
       Only on the lines seed_vehicle_id_lines links by vehicle id (D27):
       elsewhere an id can pass to another train and carry its direction.
    2. the platform: at least `direction_fill_platform_share` of its
       predictions that day, and at least `direction_fill_min_predictions`,
       list one direction. Not at a turnback: a stop that is the
       destination of any of the line's trains that day.
  Otherwise it stays null and is left out as before.

  Never filled:
    - a train terminating at the stop (destination = the stop). TfL gives
      terminating arrivals no direction, they are not departures in the
      timetable, and a train that arrives and reverses would count twice;
    - a prediction on "Platform Unknown", where the same train is also
      listed on its real platform.
  Most predictions TfL leaves without a direction have no destination
  either, so at a turnback a train ending its trip cannot be told from one
  passing through. The platform rule is off there: with it on, the District
  at Barking and the Overground termini counted more trains than the
  timetable.

  Every lookup is grouped by `dt`, so a filter on `dt` gives the same
  result as reading all of history, and a day's directions do not change
  once the day is complete. `direction_source` is listed, vehicle,
  platform or null.

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
        nullif(trim(direction), '') is null
        and coalesce(destination_naptan_id, '') <> naptan_id
        and lower(coalesce(platform_name, '')) not like '%unknown%' as is_fillable,
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
    -- The train's own direction, when its sightings agree, on lines whose
    -- vehicle ids follow one train (D27).
    select
        request_path,
        naptan_id,
        line_id,
        platform_key,
        known_vehicle_id,
        dt,
        min(listed_direction) as direction
    from listed
    where
        known_vehicle_id is not null
        and listed_direction is not null
        and line_id in (
            select line_id from {{ ref('seed_vehicle_id_lines') }}
            where link_by_vehicle_id
        )
    group by request_path, naptan_id, line_id, platform_key, known_vehicle_id, dt
    having count(distinct listed_direction) = 1
),

turnbacks as (
    -- Stops where some of the line's trains end their trip that day.
    select distinct
        line_id,
        destination_naptan_id as naptan_id,
        dt
    from {{ predictions }}
    where destination_naptan_id is not null
),

platform_counts as (
    select
        listed.request_path,
        listed.naptan_id,
        listed.line_id,
        listed.platform_key,
        listed.dt,
        listed.listed_direction,
        count(*) as predictions
    from listed
    left join turnbacks
        on
            listed.line_id = turnbacks.line_id
            and listed.naptan_id = turnbacks.naptan_id
            and listed.dt = turnbacks.dt
    where listed.listed_direction is not null and turnbacks.naptan_id is null
    group by
        listed.request_path,
        listed.naptan_id,
        listed.line_id,
        listed.platform_key,
        listed.dt,
        listed.listed_direction
),

platform_directions as (
    -- The platform's direction, when it carries one direction.
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
        listed.is_fillable
        and listed.request_path = vehicle_directions.request_path
        and listed.naptan_id = vehicle_directions.naptan_id
        and listed.line_id = vehicle_directions.line_id
        and listed.platform_key = vehicle_directions.platform_key
        and listed.known_vehicle_id = vehicle_directions.known_vehicle_id
        and listed.dt = vehicle_directions.dt
left join platform_directions
    on
        listed.is_fillable
        and listed.request_path = platform_directions.request_path
        and listed.naptan_id = platform_directions.naptan_id
        and listed.line_id = platform_directions.line_id
        and listed.platform_key = platform_directions.platform_key
        and listed.dt = platform_directions.dt
{% endmacro %}
