import json

from copilot import metrics, logbook, paths, discover
from copilot.models import JobPosting


def _seed_cache(user, data_root):
    we = paths.week_ending()
    cache = paths.postings_cache_path(user, we, data_root)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "week": we.isoformat(), "screened": 10, "ranked": 6, "filtered_out": 4,
        "postings": [
            {"title": "Ops Manager", "employer": "Cascade", "score": 5.0,
             "reasons": ["matches your skills: logistics"], "url": "u0"},
            {"title": "Program Manager", "employer": "Globex", "score": 3.2,
             "reasons": ["hits your keywords"], "url": "u1"},
        ],
    }), encoding="utf-8")


def test_funnel_top_and_summary(user_env, data_root):
    _seed_cache(user_env.user, data_root)
    logbook.append_confirmed(user_env.user, [
        {"activity_type": "applied_to_job", "employer_or_org": "Cascade",
         "position": "Ops Manager", "contact_method": "online",
         "contact_name_or_url": "u0", "result_status": "applied"},
        {"activity_type": "applied_to_job", "employer_or_org": "Globex",
         "position": "Program Manager", "contact_method": "online",
         "contact_name_or_url": "u1", "result_status": "applied"},
    ], data_root)

    f = metrics.funnel(user_env, paths.week_ending(), data_root)
    assert f["screened"] == 10 and f["ranked"] == 6 and f["applied_this_week"] == 2

    top = metrics.top_ranked(user_env, paths.week_ending(), data_root, n=3)
    assert top[0]["title"] == "Ops Manager"
    assert top[0]["reasons"]

    summ = metrics.applied_summary(user_env, data_root)
    assert summ["total_applied"] == 2
    assert ("Cascade", 1) in summ["companies"]

    md = metrics.render_markdown(user_env, paths.week_ending(), data_root)
    assert "Application funnel & metrics" in md
    assert "Cascade" in md
    assert "Screened this week:** 10" in md


def test_explain_gives_score_components_reasons(user_env):
    jp = JobPosting(source="x", title="Operations Manager", employer="Cascade",
                    description="logistics and operations work", comp_min=95000, comp_max=100000)
    hist = {"skills": ["operations", "logistics"], "roles": [{"title": "Operations Manager"}]}
    e = discover.explain(jp, user_env, hist)
    assert e["score"] > 0
    assert "comp_fit" in e["components"] and "skill_overlap" in e["components"]
    assert any("skill" in r.lower() for r in e["reasons"])
