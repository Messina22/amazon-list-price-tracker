# Amazon List Price Tracker

Track the daily prices of every item on one of your public Amazon lists, and
keep the price history in this repository so you can see how prices change
over time.

## How it works

1. You point `config.yaml` at the share link of a **public** Amazon list.
2. A GitHub Actions workflow runs once a day, fetches the list page, and
   parses out each item's title, ASIN, and current price.
3. Each item's price is appended as one row per day to
   `data/price_history.csv`, and the workflow commits that file back to the
   repository — the git history doubles as an audit trail of every price
   change.
4. Rows older than your configured retention window are deleted on every run,
   so you decide how far back the price history goes.

No servers or databases to run: GitHub Actions is the scheduler and the git
repository is the data store.

## Setup

1. **Make your Amazon list public.** On amazon.com open the list, choose
   *Invite* / *Manage list*, and set sharing so anyone with the link can view
   it. Copy the share link (it looks like
   `https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45`).

2. **Configure the tracker.** Edit `config.yaml`:

   ```yaml
   list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
   retention_days: 365   # or null to keep price history forever
   ```

3. **Enable the schedule.** Commit and push to the default branch. The
   `Track prices` workflow (`.github/workflows/track-prices.yml`) runs daily
   at 09:23 UTC; you can also trigger it manually from the Actions tab via
   *Run workflow*.

## Running locally

```bash
pip install -r requirements.txt
python -m price_tracker run       # fetch the list and record today's prices
python -m price_tracker history   # print the stored history for each item
```

Running `run` twice on the same day replaces that day's rows rather than
duplicating them.

## Data format

`data/price_history.csv` has one row per item per day:

| column      | meaning                                                        |
|-------------|----------------------------------------------------------------|
| `date`      | ISO date the price was recorded (UTC)                          |
| `key`       | stable item identity — the ASIN when known, else the list item id |
| `asin`      | Amazon product ID                                              |
| `item_id`   | Amazon's id for the entry on your list                         |
| `title`     | product title at the time of recording                         |
| `price`     | price as a decimal, empty when the item was unavailable        |
| `currency`  | ISO currency code parsed from the displayed price              |
| `available` | `true`/`false`                                                 |

`data/items.json` holds the latest snapshot of the list (titles, URLs, and
current prices) for convenience.

## Retention

`retention_days` in `config.yaml` controls how much history is kept. On every
run, rows older than that many days are deleted. Set it to `null` (or delete
the line) to keep everything forever.

## Caveats

- The list must be **public** — the tracker fetches the share page without
  logging in.
- Amazon has no official wishlist API, so this parses the public page's HTML.
  Amazon occasionally changes its markup or serves CAPTCHAs to automated
  clients; a failed run simply records nothing that day and the next run
  carries on. If runs fail repeatedly, check the Actions logs.
- Prices are whatever the list page displays (typically the default offer),
  which can differ from the price you'd see logged in with deals or coupons.

## Roadmap

- Line-graph generation of each item's price history
- Notifications (email / push) when an item drops below a threshold or goes
  on sale

## Development

```bash
pip install -r requirements.txt pytest
pytest
```
