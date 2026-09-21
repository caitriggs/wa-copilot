from copilot import claim_assist, logbook, paths


def _log_activities(user, data_root, n=3):
    types = list(logbook.ACTIVITY_TYPES)
    entries = [{"activity_type": types[i % len(types)], "employer_or_org": f"Co{i}",
                "position": "Ops", "contact_method": "online",
                "contact_name_or_url": f"http://x/{i}", "result_status": "applied"}
               for i in range(n)]
    logbook.append_confirmed(user, entries, data_root)


def test_preflight_requires_config_flag(user_env):
    ok, reason = claim_assist.preflight(user_env, confirmed=True)
    assert ok is False                       # disabled by default
    assert "disabled" in reason.lower()


def test_preflight_requires_confirmation(data_root):
    from copilot import config, paths as p
    p.ensure_scaffold("ca", data_root)
    p.config_path("ca", data_root).write_text(
        "user: ca\nsearch:\n  titles: ['x']\nclaim_assist:\n  enabled: true\n", encoding="utf-8")
    cfg = config.load("ca", data_root=data_root)
    assert claim_assist.preflight(cfg, confirmed=False) == (False, "not confirmed by the user")
    assert claim_assist.preflight(cfg, confirmed=True)[0] is True


def test_build_reference(user_env, data_root):
    _log_activities(user_env.user, data_root, 3)
    ref = claim_assist.build_reference(user_env, data_root=data_root)
    assert ref["have"] == 3
    assert ref["need"] == 3
    assert len(ref["activities"]) == 3
    # checklist mirrors the packet: exactly one pre-filled (look-for-work), rest are attestations
    kinds = [c["kind"] for c in ref["checklist"]]
    assert kinds.count("prefilled") == 1
    assert kinds.count("confirm") >= 4


def test_panel_html_is_safe_and_contains_data(user_env, data_root):
    _log_activities(user_env.user, data_root, 3)
    ref = claim_assist.build_reference(user_env, data_root=data_root)
    js = claim_assist._panel_html(ref)
    assert "never submits or certifies" in js
    assert ref["week"] in js


def test_attestation_marker_guard():
    assert claim_assist._looks_like_attestation("Were you able and available?")
    assert claim_assist._looks_like_attestation("Gross earnings this week")
    assert not claim_assist._looks_like_attestation("Employer name")
    assert not claim_assist._looks_like_attestation("Job title")
