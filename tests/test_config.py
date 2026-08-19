import pytest

from price_tracker.config import ConfigError, load_config


def write_config(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def test_load_config(tmp_path):
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
retention_days: 90
""",
    )
    config = load_config(path)
    assert config.list_url == "https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45"
    assert config.retention_days == 90
    assert str(config.history_file) == "data/price_history.csv"
    assert str(config.items_file) == "data/items.json"


def test_retention_null_means_keep_forever(tmp_path):
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
retention_days: null
""",
    )
    assert load_config(path).retention_days is None


def test_missing_list_url_raises(tmp_path):
    path = write_config(tmp_path, "list_url: ''\n")
    with pytest.raises(ConfigError, match="list_url"):
        load_config(path)


def test_invalid_retention_raises(tmp_path):
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
retention_days: -5
""",
    )
    with pytest.raises(ConfigError, match="retention_days"):
        load_config(path)


def test_notifications_default_to_off(tmp_path):
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
""",
    )
    settings = load_config(path).notifications
    assert settings.enabled is False
    assert settings.frequency == "weekly"
    assert settings.day_of_week == "monday"
    assert settings.baseline == "week"
    assert settings.channels_configured is False
    assert str(settings.state_file) == "data/notification_state.json"


def test_notification_settings_are_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SMTP_USER", "tracker@example.com")
    monkeypatch.setenv("TEST_SMTP_PASSWORD", "hunter2")
    monkeypatch.delenv("TEST_WEBHOOK_URL", raising=False)
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
notifications:
  enabled: true
  frequency: daily
  baseline: month
  min_change_percent: 2.5
  email:
    enabled: true
    to:
      - me@example.com
      - you@example.com
    from: Tracker <tracker@example.com>
    smtp_host: smtp.example.com
    smtp_port: 465
    security: ssl
    username: ${TEST_SMTP_USER}
    password: ${TEST_SMTP_PASSWORD}
  webhook:
    enabled: true
    url: ${TEST_WEBHOOK_URL}
""",
    )
    settings = load_config(path).notifications

    assert settings.enabled is True
    assert settings.frequency == "daily"
    assert settings.baseline == "month"
    assert settings.min_change_percent == 2.5
    assert settings.email.to == ["me@example.com", "you@example.com"]
    assert settings.email.smtp_port == 465
    assert settings.email.security == "ssl"
    # Secrets come from the environment, never from the committed file.
    assert settings.email.username == "tracker@example.com"
    assert settings.email.password == "hunter2"
    assert settings.email.configured is True
    # Enabled but with an unset ${VAR} is not configured — it is skipped, not
    # sent to an empty URL.
    assert settings.webhook.url == ""
    assert settings.webhook.configured is False
    assert settings.channels_configured is True


@pytest.mark.parametrize(
    "block, message",
    [
        ("  frequency: hourly", "frequency"),
        ("  day_of_week: caturday", "day_of_week"),
        ("  baseline: fortnight", "baseline"),
        ("  min_change_percent: -1", "min_change_percent"),
        ("  email:\n    security: carrier-pigeon", "security"),
    ],
)
def test_invalid_notification_settings_raise(tmp_path, block, message):
    path = write_config(
        tmp_path,
        "list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45\n"
        "notifications:\n  enabled: true\n" + block + "\n",
    )
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_baseline_accepts_a_raw_day_count(tmp_path):
    path = write_config(
        tmp_path,
        """
list_url: https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45
notifications:
  baseline: 14
""",
    )
    assert load_config(path).notifications.baseline == "14"
