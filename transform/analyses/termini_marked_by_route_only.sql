-- D70 human check: stations dim_line_station_role marks as a terminus only
-- because a route starts or ends there, with no `terminus` in
-- seed_measurement_stations. Short-working turnbacks can be caught this way
-- (Kennington, Tower Hill, Edgware Road, Arnos Grove are the ones to look
-- for): a real terminus is fine, a mid-line turnback should not be withheld.
-- Run before STATIONS_PUBLISH_ENABLED is switched on. Compile with
-- `dbt compile --select termini_marked_by_route_only --target dev` and run the
-- SQL in target/compiled with scripts/athena_query.py.

select
    line_roles.line_id,
    line_roles.naptan_id,
    named.station_name,
    line_roles.terminus_source
from {{ ref('dim_line_station_role') }} as line_roles
left join {{ ref('dim_station') }} as named
    on line_roles.naptan_id = named.naptan_id
where line_roles.terminus_source = 'route'
order by line_roles.line_id, named.station_name
