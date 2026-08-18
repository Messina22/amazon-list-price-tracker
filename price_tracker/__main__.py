"""Command-line entry point.

Usage:
    python -m price_tracker run        # scrape the list and record today's prices
    python -m price_tracker history    # print stored price history per item
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from .config import ConfigError, load_config
from .scraper import ScrapeError, scrape_list
from .storage import load_history, prune_history, record_prices, write_items_snapshot


def cmd_run(config_path: Path) -> int:
    config = load_config(config_path)
    print(f"Fetching list: {config.list_url}")
    items = scrape_list(config.list_url)
    print(f"Found {len(items)} item(s)")

    records = record_prices(config.history_file, items)
    write_items_snapshot(config.items_file, items)
    for record in records:
        price = f"{record.price} {record.currency}".strip() if record.price else "unavailable"
        print(f"  {record.date}  {price:>16}  {record.title[:70]}")

    removed = prune_history(config.history_file, config.retention_days)
    if removed:
        print(f"Pruned {removed} record(s) older than {config.retention_days} days")
    print(f"History written to {config.history_file}")
    return 0


def cmd_history(config_path: Path) -> int:
    config = load_config(config_path)
    history = load_history(config.history_file)
    if not history:
        print("No price history recorded yet. Run: python -m price_tracker run")
        return 0

    by_item = defaultdict(list)
    titles = {}
    for record in history:
        by_item[record.key].append(record)
        titles[record.key] = record.title

    for key, records in sorted(by_item.items()):
        print(f"\n{titles[key]}  [{key}]")
        for record in records:
            price = f"{record.price} {record.currency}".strip() if record.price else "unavailable"
            print(f"  {record.date}  {price}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="price_tracker")
    parser.add_argument(
        "--config", type=Path, default=Path("config.yaml"), help="path to config file"
    )
    parser.add_argument("command", choices=["run", "history"], help="what to do")
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return cmd_run(args.config)
        return cmd_history(args.config)
    except (ConfigError, ScrapeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
