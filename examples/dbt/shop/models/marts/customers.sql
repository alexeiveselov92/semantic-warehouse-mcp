-- Parsed, never run (../../dbt_project.yml): the made-up shop's customers come from examples/data/make_shop.py.
select customer_id, country, min(event_date) as signed_up_on
from {{ source('raw', 'events') }}
group by customer_id, country
