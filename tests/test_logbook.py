import pytest

from copilot import logbook, paths


def _entry(**kw):
    base = {
        "activity_type": "applied_to_job",
        "employer_or_org": "Acme",
        "position": "Ops Manager",
        "contact_method": "online",
        "contact_name_or_url": "https://acme.com/jobs/1",
        "result_status": "applied",
    }
    base.update(kw)
    return base


def test_append_and_count(data_root):
    paths.ensure_scaffold("u", data_root)
    entries = [
        _entry(contact_name_or_url="u1"),
        _entry(activity_type="linkedin_profile_update", employer_or_org="", contact_name_or_url="li"),
        _entry(activity_type="job_fair", employer_or_org="Seattle Fair", contact_name_or_url="fair"),
    ]
    added = logbook.append_confirmed("u", entries, data_root)
    assert added == 3
    we = paths.week_ending()
    assert logbook.count_valid("u", we, data_root) == 3


def test_dedup_skips_identical(data_root):
    paths.ensure_scaffold("u", data_root)
    e = _entry(contact_name_or_url="dup")
    assert logbook.append_confirmed("u", [e], data_root) == 1
    assert logbook.append_confirmed("u", [e], data_root) == 0     # duplicate skipped


def test_invalid_activity_type_rejected(data_root):
    paths.ensure_scaffold("u", data_root)
    with pytest.raises(logbook.InvalidActivity):
        logbook.append_confirmed("u", [_entry(activity_type="watched_tv")], data_root)


def test_week_isolation(data_root):
    paths.ensure_scaffold("u", data_root)
    logbook.append_confirmed("u", [_entry(week_ending="2020-01-04", contact_name_or_url="old")], data_root)
    logbook.append_confirmed("u", [_entry(contact_name_or_url="new")], data_root)
    assert logbook.count_valid("u", "2020-01-04", data_root) == 1
    assert logbook.count_valid("u", paths.week_ending(), data_root) == 1


def test_columns_are_stable(data_root):
    paths.ensure_scaffold("u", data_root)
    logbook.append_confirmed("u", [_entry()], data_root)
    rows = logbook.read_all("u", data_root)
    assert list(rows[0].keys()) == logbook.COLUMNS
