-- Parsed, never run (../../dbt_project.yml): the made-up shop's orders come from examples/data/make_shop.py.
select order_id, event_date as order_date, customer_id, currency, amount, refunded_amount
from {{ source('raw', 'events') }}
where event_name = 'order_paid'
