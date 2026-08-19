"""Render a :class:`~price_tracker.report.PriceReport` for humans.

Three renderings share one report: a plain-text digest (terminal + the
text/plain half of the email), an HTML email body, and a short webhook
message for chat apps.
"""

from __future__ import annotations

from html import escape

from .report import ItemReport, PriceReport, format_change, format_pct, format_price

_ARROWS = {"down": "▼", "up": "▲", "flat": "•"}
_COLORS = {"down": "#1a7f37", "up": "#b42318", "flat": "#57606a"}


def subject(report: PriceReport) -> str:
    """One-line summary, used as the email subject and webhook headline.

    The cadence goes in the subject (it is what inbox rules filter on); which
    average the prices are measured against goes in the body.
    """
    period = {"daily": "Daily ", "weekly": "Weekly "}.get(report.frequency, "")
    drops, rises = len(report.drops), len(report.rises)
    if not report.has_content:
        return f"{period}Amazon list report — no price data yet"

    parts = []
    if drops:
        parts.append(f"{drops} price drop{'s' if drops != 1 else ''}")
    if rises:
        parts.append(f"{rises} increase{'s' if rises != 1 else ''}")
    lows = len(report.all_time_lows)
    if lows:
        parts.append(f"{lows} all-time low{'s' if lows != 1 else ''}")
    summary = ", ".join(parts) if parts else "no significant changes"
    return f"{period}Amazon list report — {summary} ({report.as_of})"


def _window_label(report: PriceReport) -> str:
    if report.window_days == 7:
        return "past week"
    if report.window_days == 30:
        return "past month"
    return f"past {report.window_days} days"


def _item_line(item: ItemReport) -> str:
    arrow = _ARROWS[item.direction]
    price = format_price(item.current_price, item.currency)
    avg = format_price(item.baseline_average, item.currency)
    change = format_change(item.change, item.currency)
    pct = format_pct(item.change_pct)
    tags = []
    if item.is_all_time_low:
        tags.append("all-time low")
    if item.stock_change == "back_in_stock":
        tags.append("back in stock")
    if item.stock_change == "out_of_stock":
        tags.append("out of stock")
    if not item.available:
        tags.append("unavailable")
    suffix = f"  [{', '.join(tags)}]" if tags else ""
    return (
        f"  {arrow} {item.title[:60]}\n"
        f"      now {price}   avg {avg}   {change} ({pct}){suffix}"
    )


def render_text(report: PriceReport) -> str:
    """Plain-text digest."""
    window = _window_label(report)
    lines = [
        f"Amazon list price report — {report.as_of}",
        f"Current price vs. the average over the {window}.",
        "",
    ]

    if not report.has_content:
        lines.append("No prices recorded yet — nothing to compare.")
        return "\n".join(lines) + "\n"

    tracked = len(report.tracked)
    lines.append(
        f"{tracked} item(s) tracked · {len(report.drops)} cheaper · "
        f"{len(report.rises)} pricier than the {window} average"
    )
    if report.total_change is not None:
        lines.append(
            f"List total: {format_price(report.total_current, report.currency)} "
            f"vs {format_price(report.total_baseline, report.currency)} average "
            f"({format_change(report.total_change, report.currency)}, "
            f"{format_pct(report.total_change_pct)})"
        )
    lines.append("")

    if report.drops:
        lines.append(f"Price drops ({len(report.drops)})")
        lines.extend(_item_line(item) for item in report.drops)
        lines.append("")
    if report.rises:
        lines.append(f"Price increases ({len(report.rises)})")
        lines.extend(_item_line(item) for item in reversed(report.rises))
        lines.append("")

    lows = [item for item in report.all_time_lows if item not in report.drops]
    if lows:
        lines.append("All-time lows")
        lines.extend(_item_line(item) for item in lows)
        lines.append("")

    stock = [item for item in report.stock_changes]
    if stock:
        lines.append("Availability changes")
        for item in stock:
            state = "back in stock" if item.stock_change == "back_in_stock" else "out of stock"
            lines.append(f"  • {item.title[:60]} — {state}")
        lines.append("")

    lines.append("All tracked items")
    for item in sorted(report.items, key=lambda i: i.title.lower()):
        price = format_price(item.current_price, item.currency)
        avg = format_price(item.baseline_average, item.currency)
        pct = format_pct(item.change_pct)
        lines.append(f"  {item.title[:56]:<56} {price:>12} {avg:>12} {pct:>8}")

    if report.list_url:
        lines += ["", f"List: {report.list_url}"]
    lines += ["", f"Generated {report.generated_at} by amazon-list-price-tracker."]
    return "\n".join(lines) + "\n"


def _stat(label: str, value: str, color: str = "#24292f") -> str:
    return (
        '<td style="padding:12px 16px;border:1px solid #d0d7de;border-radius:8px;'
        'background:#f6f8fa;">'
        f'<div style="font:600 20px/1.2 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'color:{color};">{escape(value)}</div>'
        '<div style="font:400 12px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'color:#57606a;padding-top:2px;">{escape(label)}</div></td>'
    )


def _badges(item: ItemReport) -> str:
    badges = []
    if item.is_all_time_low:
        badges.append(("all-time low", "#1a7f37"))
    if item.stock_change == "back_in_stock":
        badges.append(("back in stock", "#0969da"))
    if item.stock_change == "out_of_stock":
        badges.append(("out of stock", "#9a6700"))
    elif not item.available:
        badges.append(("unavailable", "#57606a"))
    return "".join(
        f'<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:10px;'
        f'background:{color}1a;color:{color};font:600 11px/1.6 -apple-system,Segoe UI,'
        f'Helvetica,Arial,sans-serif;">{escape(text)}</span>'
        for text, color in badges
    )


def _row(item: ItemReport, striped: bool) -> str:
    color = _COLORS[item.direction]
    background = "#ffffff" if not striped else "#fafbfc"
    cell = (
        f'style="padding:10px 12px;border-bottom:1px solid #eaeef2;background:{background};'
        'font:400 13px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#24292f;"'
    )
    title = escape(item.title)
    if item.url:
        title = (
            f'<a href="{escape(item.url, quote=True)}" '
            f'style="color:#0969da;text-decoration:none;">{title}</a>'
        )
    return (
        "<tr>"
        f"<td {cell}>{title}{_badges(item)}</td>"
        f'<td {cell} align="right"><strong>'
        f"{escape(format_price(item.current_price, item.currency))}</strong></td>"
        f'<td {cell} align="right">'
        f"{escape(format_price(item.baseline_average, item.currency))}</td>"
        f'<td {cell} align="right" style="padding:10px 12px;border-bottom:1px solid #eaeef2;'
        f'background:{background};font:600 13px/1.4 -apple-system,Segoe UI,Helvetica,'
        f'Arial,sans-serif;color:{color};">'
        f"{escape(_ARROWS[item.direction])} {escape(format_change(item.change, item.currency))}"
        f" ({escape(format_pct(item.change_pct))})</td>"
        "</tr>"
    )


def _table(title: str, items: list[ItemReport]) -> str:
    if not items:
        return ""
    header = (
        'style="padding:8px 12px;text-align:right;border-bottom:2px solid #d0d7de;'
        'font:600 11px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'color:#57606a;text-transform:uppercase;letter-spacing:.04em;"'
    )
    rows = "".join(_row(item, index % 2 == 1) for index, item in enumerate(items))
    return (
        f'<h2 style="font:600 15px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        f'color:#24292f;margin:28px 0 8px;">{escape(title)}</h2>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;border:1px solid #d0d7de;border-radius:8px;">'
        f'<tr><th {header} align="left">Item</th><th {header}>Now</th>'
        f"<th {header}>Average</th><th {header}>Change</th></tr>"
        f"{rows}</table>"
    )


def render_html(report: PriceReport) -> str:
    """HTML email body. Inline styles only — mail clients strip <style>."""
    window = _window_label(report)
    body_font = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"

    if not report.has_content:
        return (
            f'<div style="font:400 14px/1.6 {body_font};color:#24292f;padding:24px;">'
            f"<h1 style=\"font-size:20px;margin:0 0 8px;\">Amazon list price report</h1>"
            "<p>No prices have been recorded yet, so there is nothing to compare "
            "against. The next daily run will start filling in the history.</p></div>"
        )

    change_color = _COLORS["flat"]
    if report.total_change is not None and report.total_change < 0:
        change_color = _COLORS["down"]
    elif report.total_change is not None and report.total_change > 0:
        change_color = _COLORS["up"]

    stats = "".join(
        [
            _stat("items tracked", str(len(report.tracked))),
            '<td style="width:12px;"></td>',
            _stat("cheaper than average", str(len(report.drops)), _COLORS["down"]),
            '<td style="width:12px;"></td>',
            _stat("pricier than average", str(len(report.rises)), _COLORS["up"]),
            '<td style="width:12px;"></td>',
            _stat(
                "list total vs average",
                format_change(report.total_change, report.currency),
                change_color,
            ),
        ]
    )

    lows = [item for item in report.all_time_lows if item not in report.drops]
    sections = "".join(
        [
            _table(f"Price drops ({len(report.drops)})", report.drops),
            _table(f"Price increases ({len(report.rises)})", list(reversed(report.rises))),
            _table("All-time lows", lows),
            _table("All tracked items", sorted(report.items, key=lambda i: i.title.lower())),
        ]
    )

    footer_link = (
        f'<a href="{escape(report.list_url, quote=True)}" style="color:#0969da;">'
        "View the list on Amazon</a> · "
        if report.list_url
        else ""
    )

    return (
        f'<div style="font:400 14px/1.6 {body_font};color:#24292f;'
        'background:#ffffff;padding:24px;max-width:760px;margin:0 auto;">'
        f'<h1 style="font:600 20px/1.3 {body_font};margin:0 0 4px;">'
        "Amazon list price report</h1>"
        f'<p style="color:#57606a;margin:0 0 20px;">Current prices vs. their average '
        f"over the {escape(window)} · {escape(report.as_of)}</p>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"<tr>{stats}</tr></table>"
        f"{sections}"
        f'<p style="color:#57606a;font-size:12px;margin-top:28px;">{footer_link}'
        f"Generated {escape(report.generated_at)} by amazon-list-price-tracker.</p></div>"
    )


def render_webhook_text(report: PriceReport, limit: int = 8) -> str:
    """Compact Markdown-ish message for Slack/Discord-style webhooks."""
    window = _window_label(report)
    if not report.has_content:
        return f"*Amazon list price report — {report.as_of}*\nNo price data recorded yet."

    lines = [
        f"*Amazon list price report — {report.as_of}*",
        f"Current price vs. the {window} average · {len(report.tracked)} item(s) tracked · "
        f"{len(report.drops)} cheaper, {len(report.rises)} pricier",
    ]
    highlights = report.drops + list(reversed(report.rises))
    for item in highlights[:limit]:
        low = " (all-time low)" if item.is_all_time_low else ""
        lines.append(
            f"{_ARROWS[item.direction]} {item.title[:70]} — "
            f"{format_price(item.current_price, item.currency)} "
            f"({format_pct(item.change_pct)} vs avg){low}"
        )
    if len(highlights) > limit:
        lines.append(f"…and {len(highlights) - limit} more")
    if not highlights:
        lines.append("No moves past the reporting threshold.")
    if report.list_url:
        lines.append(report.list_url)
    return "\n".join(lines)
