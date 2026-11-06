-- A successful request to a feed that is never legitimately empty must
-- produce staging rows. Column tests pass on an empty table, so without this
-- a parse that silently yields nothing (a renamed or re-cased key) would look
-- like a quiet network. Arrivals and disruptions can be empty and are left out.

with ok_polls as (
    select
        poll_id,
        request_path,
        feed
    from {{ ref('stg_polls') }}
    where
        is_ok
        and feed in ('line_status', 'ref_severity', 'ref_route_sequence')
        and dt >= current_date - interval '7' day
),

staged as (
    select distinct
        poll_id,
        request_path
    from {{ ref('stg_line_status') }}
    where dt >= current_date - interval '7' day
    union all
    select distinct
        poll_id,
        request_path
    from {{ ref('stg_severity') }}
    where dt >= current_date - interval '7' day
    union all
    select distinct
        poll_id,
        request_path
    from {{ ref('stg_route_sequence') }}
    where dt >= current_date - interval '7' day
)

select
    ok_polls.feed,
    ok_polls.poll_id,
    ok_polls.request_path
from ok_polls
left join staged
    on
        ok_polls.poll_id = staged.poll_id
        and ok_polls.request_path = staged.request_path
where staged.poll_id is null
