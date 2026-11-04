{{
    config(
        unique_key='timetable_journey_key',
        incremental_predicates=staging_incremental_predicates(),
    )
}}

with envelopes as (
    {{ raw_envelopes('ref_timetable') }}
),

-- A timetable request without a direction gets a disambiguation and no
-- `timetable` object; it unnests to no rows here and shows in stg_polls only.
routes as (
    select
        envelopes.poll_id,
        envelopes.polled_at,
        envelopes.request_path,
        envelopes.dt,
        route.route_ordinal,
        route.route_json,
        json_parse(envelopes.payload) as timetable_json
    from envelopes
    cross join
        unnest(
            coalesce(
                cast(
                    json_extract(json_parse(envelopes.payload), '$.timetable.routes')
                    as array(json)
                ),
                cast(array[] as array(json))
            )
        ) with ordinality as route (route_json, route_ordinal)
),

schedules as (
    select
        routes.poll_id,
        routes.polled_at,
        routes.request_path,
        routes.dt,
        routes.timetable_json,
        routes.route_ordinal,
        schedule.schedule_ordinal,
        schedule.schedule_json
    from routes
    cross join
        unnest(cast(json_extract(routes.route_json, '$.schedules') as array(json)))
        with ordinality as schedule (schedule_json, schedule_ordinal)
),

journeys as (
    select
        schedules.poll_id,
        schedules.polled_at,
        schedules.request_path,
        schedules.dt,
        schedules.timetable_json,
        schedules.route_ordinal,
        schedules.schedule_ordinal,
        schedules.schedule_json,
        journey.journey_ordinal,
        journey.journey_json
    from schedules
    cross join
        unnest(cast(json_extract(schedules.schedule_json, '$.knownJourneys') as array(json)))
        with ordinality as journey (journey_json, journey_ordinal)
),

parsed as (
    select
        poll_id,
        request_path,
        polled_at,
        json_extract_scalar(timetable_json, '$.lineId') as line_id,
        {{ json_text('timetable_json', '$.lineName') }} as line_name,
        json_extract_scalar(timetable_json, '$.direction') as direction,
        json_extract_scalar(timetable_json, '$.timetable.departureStopId') as departure_stop_id,
        route_ordinal,
        schedule_ordinal,
        json_extract_scalar(schedule_json, '$.name') as schedule_name,
        journey_ordinal,
        -- London service-day clock time. Hours run past 23 for trains after
        -- midnight (27 is 03:xx the next morning); int_ models resolve dates.
        cast(json_extract_scalar(journey_json, '$.hour') as integer) as departure_hour,
        cast(json_extract_scalar(journey_json, '$.minute') as integer) as departure_minute,
        cast(json_extract_scalar(journey_json, '$.intervalId') as integer) as interval_id,
        dt
    from journeys
)

select
    poll_id,
    request_path,
    polled_at,
    line_id,
    line_name,
    direction,
    departure_stop_id,
    route_ordinal,
    schedule_ordinal,
    schedule_name,
    journey_ordinal,
    departure_hour,
    departure_minute,
    interval_id,
    dt,
    departure_hour * 60 + departure_minute as departure_minute_of_service_day,
    {{ row_key([
        'poll_id', 'request_path', 'route_ordinal', 'schedule_ordinal', 'journey_ordinal',
    ]) }} as timetable_journey_key
from parsed
