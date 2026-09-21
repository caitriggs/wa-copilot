from datetime import date, timedelta

import pytest

from copilot import paths


def test_week_ending_is_always_saturday_within_the_week():
    d0 = date(2026, 7, 1)
    for i in range(40):
        d = d0 + timedelta(days=i)
        we = paths.week_ending(d)
        assert we.weekday() == 5              # Saturday
        assert 0 <= (we - d).days <= 6        # within the same Sun..Sat week


def test_week_ending_edges():
    # A Saturday maps to itself; the Sunday after starts a new week.
    sat = date(2026, 7, 25)
    assert sat.weekday() == 5
    assert paths.week_ending(sat) == sat
    sun = sat + timedelta(days=1)
    assert paths.week_ending(sun) == sat + timedelta(days=7)


def test_refuses_forbidden_drives():
    for bad in ("G:/data", "H:/x/y", "g:/lower"):
        with pytest.raises(paths.UnsafeDataLocation):
            paths.validate_local(bad)


def test_refuses_cloud_markers():
    with pytest.raises(paths.UnsafeDataLocation):
        paths.validate_local("/home/u/Google Drive/app")


def test_allows_local_path(tmp_path):
    assert paths.validate_local(tmp_path) == tmp_path


def test_safe_user_rejects_bad_ids():
    assert paths.safe_user("Jordan") == "jordan"
    for bad in ("", "../etc", "has space", "a" * 40):
        with pytest.raises(ValueError):
            paths.safe_user(bad)


def test_scaffold_creates_subdirs(data_root):
    root = paths.ensure_scaffold("someone", data_root)
    for sub in paths.SUBDIRS:
        assert (root / sub).is_dir()
