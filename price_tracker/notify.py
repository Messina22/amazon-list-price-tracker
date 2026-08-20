"""Deliver the price-change report by email and/or webhook.

Scheduling lives here too: the daily workflow calls ``notify`` every run, and
this module decides whether today is a send day (daily, or the configured
weekday for weekly digests) and records what it sent so a re-run doesn't
double-send.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .config import WEEKDAYS, EmailConfig, NotificationsConfig, WebhookConfig
from .render import render_html, render_text, render_webhook_text, subject
from .report import PriceReport, report_to_dict


class NotifyError(Exception):
    pass


@dataclass
class DeliveryResult:
    channel: str
    ok: bool
    detail: str


def load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def is_due(
    settings: NotificationsConfig, today: date | None = None, state: dict | None = None
) -> tuple[bool, str]:
    """Should a digest go out today? Returns ``(due, reason)``."""
    today = today or date.today()
    state = state or {}

    if not settings.enabled:
        return False, "notifications are disabled in config.yaml"

    last_sent = state.get("last_sent")
    if last_sent == today.isoformat():
        return False, f"a report was already sent today ({last_sent})"

    if settings.frequency == "daily":
        return True, "daily digest"

    wanted = WEEKDAYS.index(settings.day_of_week)
    if today.weekday() != wanted:
        return False, (
            f"weekly digest goes out on {settings.day_of_week.title()}, "
            f"today is {WEEKDAYS[today.weekday()].title()}"
        )
    return True, f"weekly digest ({settings.day_of_week.title()})"


def send_email(settings: EmailConfig, report: PriceReport) -> DeliveryResult:
    message = EmailMessage()
    message["Subject"] = subject(report)
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.to)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="amazon-list-price-tracker.local")
    message.set_content(render_text(report))
    message.add_alternative(render_html(report), subtype="html")

    try:
        if settings.security == "ssl":
            server = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(),
                timeout=30,
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with server:
            server.ehlo()
            if settings.security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings.username:
                server.login(settings.username, settings.password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        return DeliveryResult("email", False, f"{type(exc).__name__}: {exc}")

    return DeliveryResult("email", True, f"sent to {', '.join(settings.to)}")


def send_webhook(settings: WebhookConfig, report: PriceReport) -> DeliveryResult:
    import requests  # local import: webhooks are optional

    text = render_webhook_text(report)
    payload = {
        # "text" suits Slack/Mattermost, "content" suits Discord; senders that
        # want the numbers can read "report".
        "text": text,
        "content": text,
        "subject": subject(report),
        "report": report_to_dict(report),
    }
    try:
        response = requests.post(settings.url, json=payload, timeout=30)
        response.raise_for_status()
    except Exception as exc:  # requests raises a wide family of errors
        return DeliveryResult("webhook", False, f"{type(exc).__name__}: {exc}")
    return DeliveryResult("webhook", True, f"posted ({response.status_code})")


def deliver(settings: NotificationsConfig, report: PriceReport) -> list[DeliveryResult]:
    """Send the report on every configured channel."""
    results: list[DeliveryResult] = []
    if settings.email.configured:
        results.append(send_email(settings.email, report))
    if settings.webhook.configured:
        results.append(send_webhook(settings.webhook, report))
    if not results:
        raise NotifyError(
            "No notification channel is configured. Enable notifications.email "
            "or notifications.webhook in config.yaml."
        )
    return results


def record_sent(
    settings: NotificationsConfig,
    report: PriceReport,
    results: list[DeliveryResult],
    today: date | None = None,
) -> dict:
    """Remember the send so the same day's re-run stays quiet."""
    today = today or date.today()
    state = load_state(settings.state_file)
    state.update(
        {
            "last_sent": today.isoformat(),
            "last_frequency": settings.frequency,
            "last_baseline": report.baseline,
            "last_channels": sorted(
                result.channel for result in results if result.ok
            ),
            "last_subject": subject(report),
            "last_item_count": len(report.items),
        }
    )
    save_state(settings.state_file, state)
    return state
