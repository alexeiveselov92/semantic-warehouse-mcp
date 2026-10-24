# The shop's data — what an analyst would tell a new colleague

A made-up example of a context document: served to agents as written (`get_context`).

- Orders are paid orders only; abandoned carts are in `raw.events` (`event_name = 'cart_abandoned'`).
- Test orders from `@example.com` customers are removed in `marts.orders` already.
- Prices changed on 2026-03-01: compare revenue across that date by week, not by day.
