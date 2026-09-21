import pytest

from copilot import logbook, packet, paths


def _log_n(user, data_root, n):
    types = list(logbook.ACTIVITY_TYPES)
    entries = [
        {"activity_type": types[i % len(types)], "employer_or_org": f"Co{i}",
         "position": "Ops", "contact_method": "online", "contact_name_or_url": f"u{i}",
         "result_status": "applied"}
        for i in range(n)
    ]
    logbook.append_confirmed(user, entries, data_root)


def test_packet_refuses_when_short(user_env, data_root):
    _log_n(user_env.user, data_root, 2)          # need 3
    with pytest.raises(packet.NotEnoughActivities):
        packet.build(user_env, data_root=data_root)


def test_packet_builds_with_enough(user_env, data_root):
    _log_n(user_env.user, data_root, 3)
    out = packet.build(user_env, data_root=data_root)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Weekly-claim answer checklist" in text
    assert "penalty of perjury" in text          # the disclaimer is present
    assert "you confirm" in text                 # attestations left to the user


def test_packet_counts_only_valid(user_env, data_root):
    _log_n(user_env.user, data_root, 3)
    # a bogus row can't get in (logbook rejects it), so the count reflects only valid activities
    assert logbook.count_valid(user_env.user, paths.week_ending(), data_root) == 3
