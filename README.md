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
5. The same run refreshes `data/dashboard.json`, which the dashboard plots as
   a line graph per item.

No servers or databases to run: GitHub Actions is the scheduler, the git
repository is the data store, and the dashboard is a static page over that
data.

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
python -m price_tracker run              # fetch the list and record today's prices
python -m price_tracker history          # print the stored history for each item
python -m price_tracker dashboard        # open the price-history line graphs
```

Running `run` twice on the same day replaces that day's rows rather than
duplicating them.

## Dashboard

The tracker ships a static dashboard that plots each item's price as a line
graph from `data/price_history.csv`.

```bash
python -m price_tracker dashboard
```

That regenerates `data/dashboard.json` from the CSV and serves the UI at
http://127.0.0.1:8000/dashboard/. Pick an item in the list to see its chart.
Search, sort, and range controls filter the same history. With only one day
recorded, you get a single point; the line fills in as the daily workflow
appends more rows.

To publish the same view on GitHub Pages, set **Settings → Pages → Source** to
**GitHub Actions**. The `Deploy dashboard` workflow builds a static site on
each push to `main` that changes dashboard files or price data.

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
current prices) for convenience. `data/dashboard.json` is a derived, chart-ready
view of that same history; the dashboard reads it, and each `run` rewrites it.

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

- Notifications (email / push) when an item drops below a threshold or goes
  on sale

## Development

```bash
pip install -r requirements.txt pytest
pytest
```
