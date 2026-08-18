import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tracker import TrackerError, load_config

LIST_URL = "https://www.amazon.com/hz/wishlist/ls/1ABCD23EFGH45"


def write_config(tmp_path, **overrides):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"list_url": LIST_URL, **overrides}))
    return path


def test_load_config(tmp_path):
    config = load_config(write_config(tmp_path, retention_days=90))
    assert config.list_url == LIST_URL
    assert config.retention_days == 90
    assert config.history_file == tmp_path / "data/price_history.csv"
    assert config.items_file == tmp_path / "data/items.json"


def test_retention_null_means_keep_forever(tmp_path):
    config = load_config(write_config(tmp_path, retention_days=None))
    assert config.retention_days is None


def test_missing_list_url_raises(tmp_path):
    path = write_config(tmp_path)
    path.write_text(json.dumps({"list_url": ""}))
    with pytest.raises(TrackerError, match="list_url"):
        load_config(path)


def test_invalid_retention_raises(tmp_path):
    with pytest.raises(TrackerError, match="retention_days"):
        load_config(write_config(tmp_path, retention_days=-5))


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(TrackerError, match="not found"):
        load_config(tmp_path / "nope.json")
