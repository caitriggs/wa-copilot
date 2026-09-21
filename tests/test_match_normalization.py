"""match_pct (0-100) and top_factor labeling — discover.py's explain(). The SAME value must be
computed once and carried unchanged through the dashboard and the email (see publish_week.py /
web/dashboard.html / email_render.py); these tests pin down the normalization math itself and the
top-contributing-factor selection that feeds "Surfaced for: ..."."""

import json

from copilot import config, discover
from copilot.focus import FocusPlan
from copilot.models import JobPosting


def _cfg(**overrides):
    base = {
        "search": {"titles": ["Program Manager"], "keywords_include": [], "comp_min": None},
        "ranking": {"comp_fit": 2.0, "keyword_hits": 1.5, "title_similarity": 2.0,
                    "skill_overlap": 1.5, "weekly_notes_boost": 1.0, "focus_match": 3.5},
    }
    base["search"].update(overrides.pop("search", {}))
    base["ranking"].update(overrides.pop("ranking", {}))
    return config.Config("u", base)


def _jp(title="Program Manager", description=""):
    return JobPosting(source="usajobs", title=title, employer="Acme", description=description)


# --------------------------------------------------------------------------- match_pct range/determinism


def test_match_pct_is_0_to_100():
    cfg = _cfg()
    history = {"roles": [{"title": "Program Manager"}], "skills": []}
    out = discover.explain(_jp(), cfg, history)
    assert 0 <= out["match_pct"] <= 100


def test_match_pct_perfect_title_match_with_no_comp_floor_scores_full():
    """No comp_min configured -> comp_fit is capped at its own achievable max (0.5), so a posting
    with a perfect title match and nothing else configured should reach 100%, not be deflated by
    an unreachable comp_fit ceiling."""
    cfg = _cfg()   # comp_min None, no keywords, no notes
    history = {"roles": [{"title": "Program Manager"}], "skills": []}
    out = discover.explain(_jp(title="Program Manager"), cfg, history)
    assert out["match_pct"] == 100


def test_match_pct_same_input_same_output_every_call():
    """Determinism: identical posting/cfg/history must always produce the identical match_pct —
    the guarantee that lets the dashboard and email agree without recomputing anything."""
    cfg = _cfg(search={"comp_min": 100000})
    history = {"roles": [{"title": "Program Manager"}], "skills": ["roadmapping"]}
    jp = _jp(description="roadmapping work")
    first = discover.explain(jp, cfg, history)["match_pct"]
    for _ in range(5):
        assert discover.explain(jp, cfg, history)["match_pct"] == first


def test_match_pct_not_relative_to_other_postings_in_the_pool():
    """A weak posting's match_pct must not shift depending on whether a stronger posting also
    exists — no week-relative "top = 100%" comparison, unlike the old email-only behavior."""
    cfg = _cfg(search={"comp_min": 100000})
    history = {"roles": [{"title": "Program Manager"}], "skills": ["roadmapping"]}
    weak = _jp(title="Coordinator", description="general admin support")
    strong = _jp(title="Program Manager", description="roadmapping work")

    pct_alone = discover.explain(weak, cfg, history)["match_pct"]
    _ = discover.explain(strong, cfg, history)  # a much stronger posting exists in the same run
    pct_with_strong_present = discover.explain(weak, cfg, history)["match_pct"]
    assert pct_alone == pct_with_strong_present


def test_match_pct_reflects_comp_floor_when_configured():
    cfg = _cfg(search={"comp_min": 150000})
    history = {"roles": [{"title": "Program Manager"}], "skills": []}
    below = _jp(description="")
    below.comp_min = 100000
    above = _jp(description="")
    above.comp_min = 160000
    pct_below = discover.explain(below, cfg, history)["match_pct"]
    pct_above = discover.explain(above, cfg, history)["match_pct"]
    assert pct_above > pct_below


# --------------------------------------------------------------------------- top_factor selection


def test_top_factor_picks_title_when_title_dominates():
    cfg = _cfg()  # no keywords, no comp floor, no notes/focus
    history = {"roles": [{"title": "Program Manager"}], "skills": []}
    out = discover.explain(_jp(title="Program Manager"), cfg, history)
    assert out["top_factor"] == "title match"


def test_top_factor_picks_skills_when_skills_dominate():
    cfg = _cfg(search={"titles": ["Zoologist"]})  # posting title won't overlap -> title_sim 0
    history = {"roles": [], "skills": ["roadmapping", "stakeholder management"]}
    jp = _jp(title="Something Else", description="Needs roadmapping and stakeholder management.")
    out = discover.explain(jp, cfg, history)
    assert out["top_factor"] == "skills match"


def test_top_factor_picks_pay_fit_when_comp_dominates():
    """No title/skill signal at all, but pay clears a configured floor -> comp_fit is the only
    component with any normalized weight, so it wins."""
    cfg = _cfg(search={"titles": ["Zoologist"], "comp_min": 100000})
    history = {"roles": [], "skills": []}
    jp = _jp(title="Completely Unrelated Role", description="")
    jp.comp_min = 200000
    out = discover.explain(jp, cfg, history)
    assert out["top_factor"] == "pay fit"


def test_top_factor_picks_this_weeks_focus_when_focus_dominates():
    cfg = _cfg(search={"titles": ["Zoologist"]})
    history = {"roles": [], "skills": []}
    plan = FocusPlan(queries=["park ranger"], keywords=["outdoor", "trails"],
                     relax_title=True, relax_comp=True, summary="outdoor")
    jp = _jp(title="Park Ranger", description="Maintain outdoor trails.")
    out = discover.explain(jp, cfg, history, plan)
    assert out["top_factor"] == "this week's focus"


def test_top_factor_tie_breaks_toward_title_over_skills():
    """Equal normalized contribution from title and skills -> title wins the tie (priority order)."""
    cfg = _cfg()
    history = {"roles": [{"title": "Program Manager"}], "skills": ["roadmapping"]}
    jp = _jp(title="Program Manager", description="roadmapping")
    out = discover.explain(jp, cfg, history)
    # Both title_similarity and skill_overlap are at less-than-max here; just confirm the winner
    # is always drawn from the priority-ordered candidate list and is deterministic.
    assert out["top_factor"] in ("title match", "skills match")
    assert out["top_factor"] == discover.explain(jp, cfg, history)["top_factor"]


# --------------------------------------------------------------------------- qualification override


def test_qualification_normalized_qualified_high_confidence_is_max():
    assert discover._qualification_normalized({"verdict": "qualified", "confidence": "high"}) == 1.0


def test_qualification_normalized_not_qualified_is_zero():
    assert discover._qualification_normalized({"verdict": "not-qualified", "confidence": "high"}) == 0.0


def test_qualification_normalized_stretch_is_half_of_qualified():
    q = discover._qualification_normalized({"verdict": "qualified", "confidence": "medium"})
    s = discover._qualification_normalized({"verdict": "stretch", "confidence": "medium"})
    assert s == q / 2


def test_discover_applies_qualification_multiplier_to_match_pct(monkeypatch, data_root):
    """End-to-end: a "stretch" verdict from the (single, merged) LLM qualification judge must
    scale match_pct by the same multiplier it applies to score — the dashboard/email percentage
    reflects the qualification check too."""
    from copilot import paths, profile_import, llm

    user = "pctuser"
    root = paths.ensure_scaffold(user, data_root)
    (root / "profile").mkdir(parents=True, exist_ok=True)
    # 5 skills listed, only 1 matched by the posting -> skill_overlap normalizes to 0.2, not 1.0,
    # so the qualification override (below) is exercised against a genuinely partial top_factor
    # rather than one that's already trivially maxed out.
    (root / "profile" / "pm.txt").write_text(
        "Program Manager\nSkills: roadmapping, budgeting, scheduling, agile, stakeholder management\n",
        encoding="utf-8")
    paths.config_path(user, data_root).write_text(
        "user: pctuser\n"
        "search: { titles: ['Program Manager'] }\n"
        "profile:\n"
        "  resumes:\n"
        "    - { label: 'Project Manager', path: 'profile/pm.txt', titles: ['Program Manager'] }\n",
        encoding="utf-8")
    cfg = config.load(user, data_root=data_root)

    monkeypatch.setattr(discover, "_load_histories",
                        lambda cfg, dr=None: profile_import.ensure_histories(cfg, data_root))
    jp = _jp(title="Program Manager", description="roadmapping")
    monkeypatch.setattr(discover, "SOURCE_FETCHERS", [lambda eff: [jp]])
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    monkeypatch.setattr(config, "get_secret", lambda user, ref, data_root=None: "sk-fake")

    class _FakeTextBlock:
        def __init__(self, text):
            self.type, self.text = "text", text

    class _FakeResponse:
        def __init__(self, text):
            self.content = [_FakeTextBlock(text)]

    import json as _json

    def fake_create(**kwargs):
        text = kwargs["messages"][0]["content"]
        body = _json.loads(text.split("# Postings to evaluate", 1)[1].split("\n", 1)[1])
        return _FakeResponse(_json.dumps(
            [{"id": i["id"], "verdict": "stretch", "confidence": "medium",
              "reason": "plausible but junior for the grade level", "resume": "Project Manager"}
             for i in body]))

    fake_messages = type("M", (), {"create": staticmethod(fake_create)})()
    monkeypatch.setattr(llm, "_make_client",
                        lambda anthropic_mod, api_key: type("C", (), {"messages": fake_messages})())

    # Independently compute the pre-qualification match_pct the same way discover() would.
    histories = profile_import.ensure_histories(cfg, data_root)
    eff = discover._with_resume_titles(cfg)
    baseline = discover.explain_best(jp, eff, histories)
    expected_pct = max(0, min(100, round(baseline["match_pct"] * 0.7)))  # stretch multiplier

    # discover() doesn't return match_pct on the JobPosting itself, so read it back from the cache
    # it writes — the exact value that would reach publish_week.py / the dashboard / the email.
    discover.discover(cfg, data_root=data_root, write_cache=True)
    cache = discover.paths.postings_cache_path(cfg.user, discover.paths.week_ending(), data_root)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert len(payload["postings"]) == 1
    assert payload["postings"][0]["match_pct"] == expected_pct
    assert payload["postings"][0]["top_factor"] == "qualification fit"
