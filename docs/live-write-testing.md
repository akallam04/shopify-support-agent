# Live write testing

The deployed demo uses a read-only Shopify token, so nothing public can change the store.
Write actions are tested live only from a developer machine, against the development store,
using a second custom app whose token never leaves the local `.env`.

## 1. Mark the final-sale product

In the store admin, open **Products > Meridian Ski Goggles**, add the tag `final-sale`, and
save. The policy engine treats products with this tag as final sale, which the returns policy
says cannot be returned. Goggles are a common final-sale category for hygiene reasons, and no
existing order containing them has been delivered, so the tag changes no other scenario.

## 2. Create the write-test app

In the development store admin (`aurora-outfitters-co`):

1. Go to **Settings > Apps and sales channels > Develop apps** and choose **Create an app**.
   Name it `Aurora live write tests`.
2. Open **Configuration > Admin API integration > Configure** and select these scopes:

   | scope | used for |
   |---|---|
   | `write_orders` | `orderCreate` for test orders, `orderCancel`, `orderUpdate` (also grants `read_orders`) |
   | `write_returns` | `returnRequest` |
   | `read_returns` | reading return requests back |
   | `write_fulfillments` | delivery events on test orders |
   | `write_merchant_managed_fulfillment_orders` | fulfillments shipped from the store's own location |
   | `read_products` | resolving fixture variants and prices |
   | `read_customers` | finding the fixture customers |
   | `read_locations` | the location test orders ship from |
   | `read_all_orders` | orders dated more than 60 days back, such as the out-of-window fixture |

   `write_draft_orders` is not needed. The reseed script uses `orderCreate`, which accepts an
   order date in the past; draft orders always take the current date.
3. Save, then **Install app**. Under **API credentials**, reveal the **Admin API access token**
   (it is shown once). It is an offline token, which `orderCreate` requires.
4. Add it to the local `.env`:

   ```
   SHOPIFY_WRITE_TOKEN=shpat_...
   ```

   It stays local. `deploy/sync_lambda_env.py` refuses to push it under its own name or any
   other, and `deploy/create_function.sh` refuses to deploy if it has ended up in
   `SHOPIFY_ADMIN_TOKEN`.

## 3. Build the test scenarios

```
.venv/bin/python -m scripts.reseed_test_orders            # dry run, read-only
.venv/bin/python -m scripts.reseed_test_orders --apply    # creates the orders
```

The script checks the app's scopes first, then keeps these fixtures topped up. Every order has a
shipping address and carries the tags `v2-live-test` and `fixture-<key>`.

| fixture | state | dates (relative to the run) | tests |
|---|---|---|---|
| `return-out-of-window` | delivered | placed 50 days ago, delivered 45 days ago | return refused, outside the window |
| `return-in-window` | delivered | placed 9 days ago, delivered 5 days ago | return accepted |
| `return-final-sale` | delivered | placed 8 days ago, delivered 4 days ago | return refused, final-sale item |
| `shipped-not-delivered` | shipped | placed 4 days ago | cancel and address change refused |
| `cancel-eligible` | unfulfilled | placed 2 days ago | cancel accepted |
| `address-eligible` | unfulfilled | placed 1 day ago | address change accepted |

Delivered fixtures are marked delivered with a backdated `DELIVERED` fulfillment event. Shopify's
docs do not say whether a backdated event sets the fulfillment's delivery date, so the script
creates the oldest delivery first, reads the recorded delivery date back, and stops before
creating anything else if Shopify did not keep the backdated time. Shopify limits development
stores to five new orders a minute, so the script spaces orders 13 seconds apart.

Re-run the script before each live write session. It only recreates fixtures that are used up:
cancelled, shipped, returned, or aged out of their window. Used-up orders stay in the store.

## 4. Re-export the simulated store

The simulated store must match the live one, with its frozen clock just after the reseed run so
the in-window deliveries are still inside the window:

```
.venv/bin/python -m scripts.export_store_snapshot --frozen-now <reseed day + 1>T16:00:00Z
```

Then set `FROZEN_NOW` in `scripts/export_store_snapshot.py` to the same value, update
`SEED_HASH` in `tests/test_simdb.py`, and run both test suites:

```
.venv/bin/pytest
SHOPIFY_LIVE_TESTS=1 .venv/bin/pytest tests/test_contract_live.py
```

## If delivery dates cannot be backdated

If the script stops because Shopify recorded the real time instead of the backdated one, only the
out-of-window return is affected; every other scenario can be built. The options are:

1. **Hold the out-of-window case in the simulated store only.** Simulation tasks set the delivery
   date through their initial state. The live store has no out-of-window order, and the README
   records that difference.
2. **Let real time do the backdating.** Create the out-of-window fixture now and export after 31
   days, when its delivery has aged out of the window.
