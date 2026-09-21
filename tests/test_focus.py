"""Weekly-focus override: weekly_notes steers the week's search + ranking (focus.py, discover.py)."""

from copilot import focus, discover, config
from copilot.focus import FocusPlan
from copilot.models import JobPosting


def _cfg(**search):
    base = {
        "search": {"titles": ["Operations Manager"], "keywords_include": ["operations"],
                   "comp_min": 90000, "weekly_notes": "", "focus": {"enabled": True}},
        "ranking": {},
    }
    base["search"].update(search)
    return config.Config("u", base)


def test_heuristic_plan_extracts_queries_and_keywords():
    p = focus._heuristic_plan("explore State jobs with outdoor and remote flexibility")
    assert p.queries and isinstance(p.queries, list)
    assert "outdoor" in p.keywords and "flexibility" in p.keywords
    assert p.relax_title and p.relax_comp


def test_plan_none_when_no_notes():
    assert focus.plan(_cfg(weekly_notes="")) is None


def test_plan_none_when_disabled():
    cfg = _cfg(weekly_notes="pivot to marketing analytics", focus={"enabled": False})
    assert focus.plan(cfg) is None


def test_plan_uses_heuristic_without_api(monkeypatch):
    # No Anthropic key/SDK -> LLM plan is skipped, heuristic is used (no network in tests).
    monkeypatch.setattr("copilot.llm.has_api", lambda cfg: False)
    focus._CACHE.clear()
    p = focus.plan(_cfg(weekly_notes="marketing analytics at robotics companies"))
    assert p is not None
    assert "marketing" in p.keywords or "analytics" in p.keywords


def test_with_focus_overrides_titles_keywords_and_comp():
    cfg = _cfg()
    plan = FocusPlan(queries=["Park Ranger", "Field Technician"], keywords=["outdoor", "remote"],
                     relax_title=True, relax_comp=True, summary="outdoor state jobs")
    eff = discover._with_focus(cfg, plan)
    assert eff.get("search.titles") == ["Park Ranger", "Field Technician"]
    assert "outdoor" in eff.get("search.keywords_include")
    assert "operations" in eff.get("search.keywords_include")     # original kept
    assert eff.get("search.comp_min") is None                      # salary floor flexed
    # original config is untouched
    assert cfg.get("search.titles") == ["Operations Manager"]
    assert cfg.get("search.comp_min") == 90000


def test_with_focus_none_returns_same_config():
    cfg = _cfg()
    assert discover._with_focus(cfg, None) is cfg


def test_focus_ranks_stretch_role_above_better_title_match():
    """A focus-matching stretch role should outrank a closer title match that ignores the focus."""
    cfg = _cfg()
    history = {"roles": [{"title": "Operations Manager"}], "skills": ["operations"]}
    plan = FocusPlan(queries=["park ranger"], keywords=["outdoor", "trails"],
                     relax_title=True, relax_comp=True, summary="outdoor")

    # On-title but off-focus: matches past title exactly, no focus terms.
    on_title = JobPosting(source="usajobs", title="Operations Manager", employer="Indoor Corp",
                          location="Seattle, WA", description="Office operations role.")
    # Off-title but on-focus: a plausible stretch that hits the week's focus terms.
    on_focus = JobPosting(source="usajobs", title="Park Ranger", employer="State Parks",
                          location="WA", description="Maintain outdoor trails; some operations duties.")

    s_title = discover.explain(on_title, cfg, history, plan)["score"]
    s_focus = discover.explain(on_focus, cfg, history, plan)["score"]
    assert s_focus > s_title


def test_explain_without_focus_is_unchanged_shape():
    cfg = _cfg()
    history = {"roles": [{"title": "Operations Manager"}], "skills": ["operations"]}
    jp = JobPosting(source="usajobs", title="Operations Manager", employer="C",
                    location="Seattle, WA", description="operations")
    out = discover.explain(jp, cfg, history)     # focus=None
    assert out["components"]["focus_match"] == 0
    assert "score" in out and out["reasons"]
