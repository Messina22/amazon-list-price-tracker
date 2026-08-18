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
