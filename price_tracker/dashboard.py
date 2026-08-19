"""Dashboard data and local/static site helpers.

The daily tracker writes ``data/dashboard.json``, a JSON view of the CSV
history that the static dashboard in ``dashboard/`` can plot as line graphs.
``python -m price_tracker dashboard`` serves that UI locally.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .storage import PriceRecord, load_history

DASHBOARD_DIR = Path("dashboard")


def _parse_price(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _record_price(record: PriceRecord) -> float | None:
    return _parse_price(record.price)


def build_dashboard_data(
    history: list[PriceRecord],
    snapshot: list[dict] | None = None,
    list_url: str = "",
) -> dict:
    """Shape CSV history + the latest item snapshot into chart-ready JSON."""
    snapshot = snapshot or []
    snapshot_by_key = {item.get("key"): item for item in snapshot if item.get("key")}

    grouped: dict[str, list[PriceRecord]] = defaultdict(list)
    for record in history:
        grouped[record.key].append(record)
    for records in grouped.values():
        records.sort(key=lambda record: record.date)

    keys: list[str] = []
    seen: set[str] = set()
    for item in snapshot:
        key = item.get("key")
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    for key in grouped:
        if key not in seen:
            keys.append(key)
            seen.add(key)

    items = []
    for key in keys:
        records = grouped.get(key, [])
        snap = snapshot_by_key.get(key, {})
        latest = records[-1] if records else None

        series = [
            {
                "date": record.date,
                "price": _record_price(record),
                "available": record.available == "true",
            }
            for record in records
        ]
        numeric = [point["price"] for point in series if point["price"] is not None]

        current = _parse_price(snap.get("price"))
        if current is None and numeric:
            current = numeric[-1]

        first = numeric[0] if numeric else None
        change = None
        change_pct = None
        if current is not None and first is not None and len(numeric) >= 2:
            change = round(current - first, 2)
            if first != 0:
                change_pct = round((current - first) / first * 100, 2)

        available = snap.get("available")
        if available is None:
            available = latest.available == "true" if latest else False

        items.append(
            {
                "key": key,
                "asin": snap.get("asin") or (latest.asin if latest else ""),
                "title": snap.get("title") or (latest.title if latest else key),
                "url": snap.get("url") or "",
                "currency": snap.get("currency")
                or (latest.currency if latest else "")
                or "USD",
                "available": bool(available),
                "currentPrice": current,
                "firstPrice": first,
                "minPrice": min(numeric) if numeric else None,
                "maxPrice": max(numeric) if numeric else None,
                "change": change,
                "changePct": change_pct,
                "history": series,
            }
        )

    dates = [record.date for record in history]
    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "listUrl": list_url,
        "lastDate": max(dates) if dates else None,
        "itemCount": len(items),
        "availableCount": sum(1 for item in items if item["available"]),
        "items": items,
    }


def write_dashboard_json(
    history_file: Path,
    items_file: Path,
    output_file: Path | None = None,
    list_url: str = "",
) -> Path:
    """Write ``dashboard.json`` next to the CSV (or to ``output_file``)."""
    output_file = output_file or history_file.parent / "dashboard.json"
    snapshot: list[dict] = []
    if items_file.exists():
        snapshot = json.loads(items_file.read_text())
        if not isinstance(snapshot, list):
            snapshot = []
    payload = build_dashboard_data(
        load_history(history_file), snapshot, list_url=list_url
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, indent=2) + "\n")
    return output_file


def build_dashboard_site(
    history_file: Path,
    items_file: Path,
    out_dir: Path,
    list_url: str = "",
    dashboard_dir: Path = DASHBOARD_DIR,
) -> Path:
    """Copy the static UI and generated JSON into ``out_dir`` for GitHub Pages."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        source = dashboard_dir / name
        if not source.exists():
            raise FileNotFoundError(f"Dashboard file missing: {source}")
        shutil.copyfile(source, out_dir / name)

    data_dir = out_dir / "data"
    data_dir.mkdir(exist_ok=True)
    write_dashboard_json(history_file, items_file, data_dir / "dashboard.json", list_url)
    if history_file.exists():
        shutil.copyfile(history_file, data_dir / history_file.name)
    if items_file.exists():
        shutil.copyfile(items_file, data_dir / items_file.name)
    return out_dir


class _DashboardHandler(SimpleHTTPRequestHandler):
    """Serve the repo so ``/dashboard/`` and ``/data/`` both resolve."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/dashboard/")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def serve_dashboard(port: int = 8000, directory: Path | None = None) -> None:
    """Serve the dashboard until interrupted."""
    directory = str((directory or Path.cwd()).resolve())

    class Handler(_DashboardHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped dashboard server")
    finally:
        server.server_close()
