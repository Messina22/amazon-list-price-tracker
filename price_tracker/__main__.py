"""Command-line entry point.

Usage:
    python -m price_tracker run              # scrape the list and record today's prices
    python -m price_tracker history          # print stored price history per item
    python -m price_tracker dashboard        # open the price-history line graphs
    python -m price_tracker build-dashboard  # write a static dashboard site
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from collections import defaultdict
from pathlib import Path

from .config import ConfigError, load_config
from .dashboard import build_dashboard_site, serve_dashboard, write_dashboard_json
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

    dashboard_file = write_dashboard_json(
        config.history_file, config.items_file, list_url=config.list_url
    )
    print(f"History written to {config.history_file}")
    print(f"Dashboard data written to {dashboard_file}")
    return 0


def cmd_dashboard(config_path: Path, port: int, open_browser: bool) -> int:
    config = load_config(config_path)
    dashboard_file = write_dashboard_json(
        config.history_file, config.items_file, list_url=config.list_url
    )
    url = f"http://127.0.0.1:{port}/dashboard/"
    print(f"Wrote {dashboard_file}", flush=True)
    print(f"Serving dashboard at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    serve_dashboard(port=port)
    return 0


def cmd_build_dashboard(config_path: Path, out_dir: Path) -> int:
    config = load_config(config_path)
    build_dashboard_site(
        config.history_file, config.items_file, out_dir, list_url=config.list_url
    )
    print(f"Static dashboard written to {out_dir}")
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
    parser.add_argument(
        "command",
        choices=["run", "history", "dashboard", "build-dashboard"],
        help="what to do",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="port for the dashboard server"
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="do not open a browser when serving the dashboard",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("_site"),
        help="output directory for build-dashboard",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return cmd_run(args.config)
        if args.command == "history":
            return cmd_history(args.config)
        if args.command == "dashboard":
            return cmd_dashboard(args.config, port=args.port, open_browser=not args.no_open)
        return cmd_build_dashboard(args.config, args.out)
    except (ConfigError, ScrapeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
