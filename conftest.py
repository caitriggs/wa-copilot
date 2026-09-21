"""Pytest bootstrap: make `copilot` importable and provide an isolated data root.

Tests never touch a real Desktop or the network — they use a temp data_root passed explicitly.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

VALID_CONFIG = """\
user: testuser
display_name: "Test User"
email:
  enabled: false
  to: ""
search:
  titles: ["Operations Manager"]
  locations: ["Seattle, WA"]
  keywords_include: ["operations"]
  keywords_exclude: ["commission-only"]
  comp_min: 50000
targets_per_week: 3
sources:
  usajobs: { enabled: true }
  worksourcewa: { enabled: true, saved_search_rss: "" }
  feeds: { enabled: false, rss_urls: [] }
"""


@pytest.fixture()
def data_root(tmp_path):
    return tmp_path / "app-data"


@pytest.fixture()
def user_env(data_root):
    """Scaffold user 'testuser' with a valid config under a temp data root. Returns a Config."""
    from copilot import paths, config
    user = "testuser"
    paths.ensure_scaffold(user, data_root)
    paths.config_path(user, data_root).write_text(VALID_CONFIG, encoding="utf-8")
    cfg = config.load(user, data_root=data_root)
    return cfg
