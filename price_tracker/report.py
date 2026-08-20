"""Price-change reporting.

Turns the CSV history into a digest that answers the question the daily rows
don't: *is this a good price right now?* For every item we compare the current
price against its average over a trailing window (the past week or the past
month), and surface the movers, the all-time lows, and the stock changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .storage import PriceRecord

# Named baseline windows offered in config.yaml, in days.
BASELINE_WINDOWS = {"week": 7, "month": 30}
DEFAULT_BASELINE = "week"

_CURRENCY_SYMBOLS = {
    "USD": "$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
    "INR": "₹",
    "CAD": "C$",
    "AUD": "A$",
}


def baseline_days(baseline: str) -> int:
    """Days in a named baseline window ("week"/"month"), or a raw day count."""
    key = str(baseline).strip().lower()
    if key in BASELINE_WINDOWS:
        return BASELINE_WINDOWS[key]
    try:
        days = int(key)
    except (TypeError, ValueError):
        raise ValueError(
            f"Unknown baseline {baseline!r}: use 'week', 'month', or a number of days"
        )
    if days <= 0:
        raise ValueError("baseline must be a positive number of days")
    return days


def format_price(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper())
    if symbol:
        return f"{symbol}{value:,.2f}"
    return f"{value:,.2f} {currency}".strip()


def format_change(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{format_price(abs(value), currency)}"


def format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}{abs(value):.1f}%"


@dataclass
class ItemReport:
    key: str
    asin: str
    title: str
    url: str
    currency: str
    available: bool
    current_price: float | None
    baseline_average: float | None
    baseline_samples: int
    change: float | None
    change_pct: float | None
    previous_price: float | None
    previous_change: float | None
    window_min: float | None
    window_max: float | None
    all_time_min: float | None
    all_time_max: float | None
    is_all_time_low: bool
    stock_change: str | None  # "back_in_stock" | "out_of_stock" | None
    points: int

    @property
    def direction(self) -> str:
        if self.change is None or self.change == 0:
            return "flat"
        return "down" if self.change < 0 else "up"

    @property
    def abs_change_pct(self) -> float:
        return abs(self.change_pct) if self.change_pct is not None else 0.0


@dataclass
class PriceReport:
    generated_at: str
    as_of: str
    baseline: str
    window_days: int
    list_url: str
    min_change_percent: float
    frequency: str = ""  # "daily" / "weekly", purely for labelling the digest
    items: list[ItemReport] = field(default_factory=list)

    @property
    def tracked(self) -> list[ItemReport]:
        """Items with enough data to say something about the price."""
        return [item for item in self.items if item.current_price is not None]

    @property
    def movers(self) -> list[ItemReport]:
        """Items whose move from the baseline clears the configured threshold."""
        moved = [
            item
            for item in self.items
            if item.change is not None
            and item.abs_change_pct >= self.min_change_percent
            and item.change != 0
        ]
        moved.sort(key=lambda item: (item.change_pct if item.change_pct else 0.0))
        return moved

    @property
    def drops(self) -> list[ItemReport]:
        return [item for item in self.movers if item.direction == "down"]

    @property
    def rises(self) -> list[ItemReport]:
        return [item for item in self.movers if item.direction == "up"]

    @property
    def all_time_lows(self) -> list[ItemReport]:
        return [item for item in self.items if item.is_all_time_low]

    @property
    def stock_changes(self) -> list[ItemReport]:
        return [item for item in self.items if item.stock_change]

    @property
    def total_current(self) -> float | None:
        values = [
            item.current_price
            for item in self.items
            if item.current_price is not None and item.baseline_average is not None
        ]
        return round(sum(values), 2) if values else None

    @property
    def total_baseline(self) -> float | None:
        values = [
            item.baseline_average
            for item in self.items
            if item.current_price is not None and item.baseline_average is not None
        ]
        return round(sum(values), 2) if values else None

    @property
    def total_change(self) -> float | None:
        if self.total_current is None or self.total_baseline is None:
            return None
        return round(self.total_current - self.total_baseline, 2)

    @property
    def total_change_pct(self) -> float | None:
        if self.total_change is None or not self.total_baseline:
            return None
        return round(self.total_change / self.total_baseline * 100, 2)

    @property
    def currency(self) -> str:
        for item in self.items:
            if item.currency:
                return item.currency
        return "USD"

    @property
    def has_content(self) -> bool:
        """True once there is anything to say — a price now, or a price before.

        An item that just went out of stock has no current price but is still
        worth reporting, so this is deliberately wider than ``tracked``.
        """
        return any(item.current_price is not None or item.points for item in self.items)


def _price_of(record: PriceRecord) -> float | None:
    if not record.price:
        return None
    try:
        return round(float(record.price), 2)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _build_item(
    key: str,
    records: list[PriceRecord],
    snap: dict,
    as_of: date,
    window_days: int,
) -> ItemReport:
    records = sorted(records, key=lambda record: record.date)
    as_of_iso = as_of.isoformat()
    window_start = (as_of - timedelta(days=window_days)).isoformat()

    latest = records[-1] if records else None
    priced = [(record, _price_of(record)) for record in records]
    all_prices = [price for _, price in priced if price is not None]

    # Prices strictly before today, inside the trailing window: the baseline
    # we compare "right now" against.
    window = [
        price
        for record, price in priced
        if price is not None and window_start <= record.date < as_of_iso
    ]
    baseline = _mean(window)

    available = snap.get("available")
    if available is None:
        available = latest.available == "true" if latest else False
    available = bool(available)

    # An item that is unavailable right now has no current price — falling
    # back to the last price we saw would report a price you cannot pay.
    current = None
    if available:
        snap_price = snap.get("price")
        if snap_price not in (None, ""):
            try:
                current = round(float(snap_price), 2)
            except (TypeError, ValueError):
                current = None
        if current is None:
            for record, price in reversed(priced):
                if price is not None and record.date <= as_of_iso:
                    current = price
                    break

    previous = None
    for record, price in reversed(priced):
        if price is not None and record.date < as_of_iso:
            previous = price
            break

    change = change_pct = None
    if current is not None and baseline is not None:
        change = round(current - baseline, 2)
        if baseline:
            change_pct = round(change / baseline * 100, 2)

    previous_change = (
        round(current - previous, 2)
        if current is not None and previous is not None
        else None
    )

    previous_available = None
    for record in reversed(records):
        if record.date < as_of_iso:
            previous_available = record.available == "true"
            break

    stock_change = None
    if previous_available is not None and previous_available != available:
        stock_change = "back_in_stock" if available else "out_of_stock"

    # Everything we know about this item's price, including right now: when
    # notify runs after the daily record, today's row is already in the
    # history, but a report built from a fresh snapshot alone still counts.
    observed = all_prices + ([current] if current is not None else [])
    all_time_min = min(observed) if observed else None
    all_time_max = max(observed) if observed else None
    # A price that has never moved is not news, so require the item to have
    # actually been more expensive at some point before calling it a low.
    is_all_time_low = bool(
        current is not None
        and len(observed) >= 2
        and all_time_max > all_time_min
        and current <= all_time_min + 0.005
    )

    return ItemReport(
        key=key,
        asin=snap.get("asin") or (latest.asin if latest else ""),
        title=snap.get("title") or (latest.title if latest else key),
        url=snap.get("url") or "",
        currency=(snap.get("currency") or (latest.currency if latest else "") or "USD"),
        available=available,
        current_price=current,
        baseline_average=baseline,
        baseline_samples=len(window),
        change=change,
        change_pct=change_pct,
        previous_price=previous,
        previous_change=previous_change,
        window_min=min(window) if window else None,
        window_max=max(window) if window else None,
        all_time_min=all_time_min,
        all_time_max=all_time_max,
        is_all_time_low=is_all_time_low,
        stock_change=stock_change,
        points=len(all_prices),
    )


def build_report(
    history: list[PriceRecord],
    snapshot: list[dict] | None = None,
    baseline: str = DEFAULT_BASELINE,
    as_of: date | None = None,
    list_url: str = "",
    min_change_percent: float = 0.0,
    frequency: str = "",
) -> PriceReport:
    """Compare each item's current price against its trailing-window average."""
    window_days = baseline_days(baseline)
    as_of = as_of or date.today()
    snapshot = snapshot or []
    snapshot_by_key = {item.get("key"): item for item in snapshot if item.get("key")}

    grouped: dict[str, list[PriceRecord]] = {}
    for record in history:
        grouped.setdefault(record.key, []).append(record)

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

    items = [
        _build_item(key, grouped.get(key, []), snapshot_by_key.get(key, {}), as_of, window_days)
        for key in keys
    ]
    items.sort(key=lambda item: (item.change_pct if item.change_pct is not None else 0.0))

    return PriceReport(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        as_of=as_of.isoformat(),
        baseline=str(baseline).lower(),
        window_days=window_days,
        list_url=list_url,
        min_change_percent=float(min_change_percent or 0.0),
        frequency=str(frequency or "").lower(),
        items=items,
    )


def report_to_dict(report: PriceReport) -> dict:
    """JSON-serializable view of the report (used by webhooks and --json)."""
    return {
        "generatedAt": report.generated_at,
        "asOf": report.as_of,
        "baseline": report.baseline,
        "windowDays": report.window_days,
        "frequency": report.frequency,
        "listUrl": report.list_url,
        "currency": report.currency,
        "itemCount": len(report.items),
        "trackedCount": len(report.tracked),
        "dropCount": len(report.drops),
        "riseCount": len(report.rises),
        "allTimeLowCount": len(report.all_time_lows),
        "totalCurrent": report.total_current,
        "totalBaseline": report.total_baseline,
        "totalChange": report.total_change,
        "totalChangePct": report.total_change_pct,
        "items": [
            {
                "key": item.key,
                "asin": item.asin,
                "title": item.title,
                "url": item.url,
                "currency": item.currency,
                "available": item.available,
                "currentPrice": item.current_price,
                "baselineAverage": item.baseline_average,
                "baselineSamples": item.baseline_samples,
                "change": item.change,
                "changePct": item.change_pct,
                "previousPrice": item.previous_price,
                "previousChange": item.previous_change,
                "windowMin": item.window_min,
                "windowMax": item.window_max,
                "allTimeMin": item.all_time_min,
                "allTimeMax": item.all_time_max,
                "isAllTimeLow": item.is_all_time_low,
                "stockChange": item.stock_change,
                "direction": item.direction,
                "points": item.points,
            }
            for item in report.items
        ],
    }
