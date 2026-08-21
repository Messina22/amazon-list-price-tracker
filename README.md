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
6. Optionally, the run emails or webhooks you a digest — daily or weekly —
   comparing each item's current price against its average over the past week
   or month.

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

4. **Optional — turn on notifications.** See
   [Notifications](#notifications) to get a daily or weekly digest of how
   prices compare with their recent average.

## Running locally

```bash
pip install -r requirements.txt
python -m price_tracker run              # fetch the list and record today's prices
python -m price_tracker history          # print the stored history for each item
python -m price_tracker dashboard        # open the price-history line graphs
python -m price_tracker report           # print the price-change digest
python -m price_tracker notify           # email/webhook the digest if one is due
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

To publish the same view on GitHub Pages:

1. Open **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
   (A workflow token cannot turn Pages on by itself; this is a one-time
   repository setting.)
3. Re-run the `Deploy dashboard` workflow, or push a change to dashboard
   files / price data on `main`.

Until Pages is enabled, that workflow still builds the site and exits
successfully — it just skips publishing and leaves a warning on the run.

## Notifications

Get a digest of how each item's price is doing right now compared with its
recent average — by email, by webhook (Slack, Discord, ntfy, anything that
takes a JSON POST), or both.

Each item in the digest shows:

- **current price** vs. its **average over the trailing window** (the past week
  or the past month), as an absolute change and a percentage
- the **cheapest and priciest** it was inside that window
- an **all-time low** badge when today's price matches the lowest ever recorded
- **availability changes** — back in stock, or newly unavailable
- a **list total**: what the whole list costs now vs. what it averaged

Items are sorted biggest drop first, and the subject line summarises the run
("Weekly Amazon list report — 3 price drops, 1 increase, 2 all-time lows").

### Configure

Everything lives under `notifications:` in `config.yaml`:

```yaml
notifications:
  enabled: true
  frequency: weekly        # daily | weekly
  day_of_week: monday      # which day weekly digests go out
  baseline: week           # week (7d) | month (30d) | a number of days
  min_change_percent: 1.0  # ignore moves smaller than this
  email:
    enabled: true
    to:
      - you@example.com
    from: Amazon Price Tracker <you@example.com>
    smtp_host: smtp.gmail.com
    smtp_port: 587
    security: starttls     # starttls | ssl | none
    username: ${SMTP_USERNAME}
    password: ${SMTP_PASSWORD}
  webhook:
    enabled: true
    url: ${PRICE_TRACKER_WEBHOOK_URL}
```

`frequency` decides how often you hear from the tracker; `baseline` decides
what "average" the current price is measured against. They are independent —
a daily email compared against the past month is a perfectly good setup.

### Secrets

Any `${VAR}` in the `notifications` block is read from the environment, so no
credential is ever committed. Add them under **Settings → Secrets and
variables → Actions**:

| secret | used for |
|--------|----------|
| `SMTP_USERNAME` | SMTP login |
| `SMTP_PASSWORD` | SMTP password — for Gmail, an [app password](https://support.google.com/accounts/answer/185833), not your account password |
| `PRICE_TRACKER_WEBHOOK_URL` | webhook endpoint |

A channel that is `enabled: true` but whose `${VAR}` is unset counts as not
configured: it is skipped rather than failing the run.

### Scheduling

The daily `Track prices` workflow calls `python -m price_tracker notify` right
after recording prices. That command is a no-op unless today is a send day, so
a weekly digest still goes out from a daily workflow. The last send is recorded
in `data/notification_state.json` and committed, so re-running the workflow on
the same day does not send twice.

To send one on demand — handy for checking secrets — run the **Send price
report** workflow from the Actions tab. It defaults to a dry run that prints
the digest into the job log without emailing anyone.

### Trying it locally

```bash
python -m price_tracker report                     # print the digest
python -m price_tracker report --baseline month    # compare against 30 days
python -m price_tracker report --format html > preview.html
python -m price_tracker notify --dry-run --force   # build and print, send nothing
python -m price_tracker notify --force             # send it now
```

Webhook payloads carry `text` (Slack-style), `content` (Discord-style), and a
full `report` object with every number in the digest, so a custom consumer can
format its own message.

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
`data/notification_state.json` records when the last digest went out.

## Retention

`retention_days` in `config.yaml` controls how much history is kept. On every
run, rows older than that many days are deleted. Set it to `null` (or delete
the line) to keep everything forever.

## Caveats

- The list must be **public** — the tracker fetches the share page without
  logging in.
- Amazon has no official wishlist API, so this parses the public page's HTML.
  GitHub-hosted runners are often served a CAPTCHA when the client looks like
  `python-requests`; the tracker impersonates a Chrome TLS fingerprint and
  retries a few times on CAPTCHA / 429 / 503. If a run still cannot fetch the
  list, it fails that day and the next scheduled run tries again. Check the
  Actions logs if that happens repeatedly. Amazon also changes its markup
  from time to time.
- Prices are whatever the list page displays (typically the default offer),
  which can differ from the price you'd see logged in with deals or coupons.

## Roadmap

- Per-item price alerts ("tell me the moment this drops below $40")

## Development

```bash
pip install -r requirements.txt pytest
pytest
```
