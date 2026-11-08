{{ config(materialized='table') }}

select
    line_id,
    line_name,
    mode_name,
    colour,
    is_low_volume
from {{ ref('lines') }}
