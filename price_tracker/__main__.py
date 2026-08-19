"""Command-line entry point.

Usage:
    python -m price_tracker run              # scrape the list and record today's prices
    python -m price_tracker history          # print stored price history per item
    python -m price_tracker dashboard        # open the price-history line graphs
    python -m price_tracker build-dashboard  # write a static dashboard site
    python -m price_tracker notify           # email/webhook the price-change digest
    python -m price_tracker report           # print the same digest to the terminal
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections import defaultdict
from pathlib import Path

from .config import Config, ConfigError, load_config
from .dashboard import build_dashboard_site, serve_dashboard, write_dashboard_json
from .notify import NotifyError, deliver, is_due, load_state, record_sent
from .render import render_html, render_text, render_webhook_text, subject
from .report import build_report, report_to_dict
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


def _load_snapshot(config: Config) -> list[dict]:
    if not config.items_file.exists():
        return []
    try:
        snapshot = json.loads(config.items_file.read_text())
    except json.JSONDecodeError:
        return []
    return snapshot if isinstance(snapshot, list) else []


def _build_report(config: Config, baseline: str | None = None):
    settings = config.notifications
    return build_report(
        load_history(config.history_file),
        _load_snapshot(config),
        baseline=baseline or settings.baseline,
        list_url=config.list_url,
        min_change_percent=settings.min_change_percent,
        frequency=settings.frequency,
    )


def cmd_report(config_path: Path, baseline: str | None, output: str) -> int:
    """Print the digest without sending it anywhere."""
    config = load_config(config_path)
    report = _build_report(config, baseline)
    if output == "html":
        print(render_html(report))
    elif output == "json":
        print(json.dumps(report_to_dict(report), indent=2))
    elif output == "webhook":
        print(render_webhook_text(report))
    else:
        print(render_text(report), end="")
    return 0


def cmd_notify(
    config_path: Path, baseline: str | None, force: bool, dry_run: bool
) -> int:
    config = load_config(config_path)
    settings = config.notifications

    if not force:
        due, reason = is_due(settings, state=load_state(settings.state_file))
        if not due:
            print(f"No notification sent: {reason}")
            return 0
        print(f"Sending: {reason}")

    report = _build_report(config, baseline)
    if not report.has_content:
        print("No price data recorded yet — nothing to report.")
        return 0

    print(f"Subject: {subject(report)}")
    if dry_run:
        print("Dry run — nothing delivered. Report follows:\n")
        print(render_text(report), end="")
        return 0

    results = deliver(settings, report)
    for result in results:
        status = "ok" if result.ok else "FAILED"
        print(f"  {result.channel}: {status} — {result.detail}")

    if not any(result.ok for result in results):
        print("Error: every notification channel failed", file=sys.stderr)
        return 1

    record_sent(settings, report, results)
    print(f"Recorded send in {settings.state_file}")
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
        choices=["run", "history", "dashboard", "build-dashboard", "notify", "report"],
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
    parser.add_argument(
        "--baseline",
        help="override the comparison window for notify/report: week, month, or days",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="send the notification even when today is not a scheduled send day",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the notification and print it instead of sending it",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["text", "html", "json", "webhook"],
        default="text",
        help="output format for the report command",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return cmd_run(args.config)
        if args.command == "history":
            return cmd_history(args.config)
        if args.command == "dashboard":
            return cmd_dashboard(args.config, port=args.port, open_browser=not args.no_open)
        if args.command == "notify":
            return cmd_notify(
                args.config, args.baseline, force=args.force, dry_run=args.dry_run
            )
        if args.command == "report":
            return cmd_report(args.config, args.baseline, args.output_format)
        return cmd_build_dashboard(args.config, args.out)
    except (ConfigError, NotifyError, ScrapeError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
