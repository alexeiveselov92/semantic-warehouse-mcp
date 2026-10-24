# Grouping revenue

- Never add amounts of different currencies: group by `currency`, or name the one the question asks about.
- A refund belongs to the day of its order, not the day it was made.
- By country: join `marts.customers` on `customer_id`; a customer without a country is shown as `unknown`.
