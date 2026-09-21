"""Multi-resume support: config.resumes(), per-résumé histories, and one-pool best-résumé ranking."""

from copilot import config, paths, profile_import, discover
from copilot.config import Config
from copilot.models import JobPosting


# --------------------------------------------------------------------------- config.resumes()


def test_resumes_defaults_to_single(data_root):
    paths.ensure_scaffold("r1", data_root)
    paths.config_path("r1", data_root).write_text(
        "user: r1\nsearch:\n  titles: ['x']\n", encoding="utf-8")
    cfg = config.load("r1", data_root=data_root)
    rs = cfg.resumes()
    assert len(rs) == 1
    assert rs[0]["slug"] == "resume" and rs[0]["path"] == "profile/resume.pdf"


def test_resumes_list_with_labels_slugs_titles(data_root):
    paths.ensure_scaffold("r2", data_root)
    paths.config_path("r2", data_root).write_text(
        "user: r2\n"
        "search: { titles: ['x'] }\n"
        "profile:\n"
        "  resumes:\n"
        "    - { label: 'Test Engineer', path: 'profile/te.pdf', titles: ['Test Engineer', 'SDET'] }\n"
        "    - { label: 'QA Test Lead',  path: 'profile/qa.pdf', titles: ['QA Lead'] }\n",
        encoding="utf-8")
    cfg = config.load("r2", data_root=data_root)
    rs = cfg.resumes()
    assert [r["label"] for r in rs] == ["Test Engineer", "QA Test Lead"]
    assert [r["slug"] for r in rs] == ["test-engineer", "qa-test-lead"]
    assert rs[0]["titles"] == ["Test Engineer", "SDET"]


# --------------------------------------------------------------------------- per-résumé histories


def _cfg_with_two_resumes(data_root, user="r3"):
    root = paths.ensure_scaffold(user, data_root)
    (root / "profile").mkdir(parents=True, exist_ok=True)
    (root / "profile" / "te.txt").write_text(
        "Test Engineer\nSkills: pytest, selenium, automation, python\n", encoding="utf-8")
    (root / "profile" / "qa.txt").write_text(
        "QA Manager\nSkills: test strategy, leadership, jira, roadmap\n", encoding="utf-8")
    paths.config_path(user, data_root).write_text(
        "user: %s\n"
        "search: { titles: ['x'] }\n"
        "profile:\n"
        "  resumes:\n"
        "    - { label: 'Test Engineer', path: 'profile/te.txt', titles: ['Test Engineer'] }\n"
        "    - { label: 'QA Lead',       path: 'profile/qa.txt', titles: ['QA Lead'] }\n" % user,
        encoding="utf-8")
    return config.load(user, data_root=data_root)


def test_ensure_histories_builds_one_per_resume(data_root):
    cfg = _cfg_with_two_resumes(data_root)
    hs = profile_import.ensure_histories(cfg, data_root)
    assert [h["label"] for h in hs] == ["Test Engineer", "QA Lead"]
    # distinct skills parsed from each résumé
    te_skills = " ".join(hs[0]["history"]["skills"]).lower()
    qa_skills = " ".join(hs[1]["history"]["skills"]).lower()
    assert "selenium" in te_skills and "selenium" not in qa_skills
    assert "leadership" in qa_skills
    # per-slug files written, plus legacy history.json mirroring the first
    root = paths.user_root("r3", data_root) / "profile"
    assert (root / "history.test-engineer.json").exists()
    assert (root / "history.qa-lead.json").exists()
    assert (root / "history.json").exists()


def test_ensure_history_single_returns_first(data_root):
    cfg = _cfg_with_two_resumes(data_root, user="r4")
    hist = profile_import.ensure_history(cfg, data_root)
    assert hist.get("resume_label") == "Test Engineer"


# --------------------------------------------------------------------------- one-pool ranking


def test_explain_best_picks_higher_scoring_resume(data_root):
    cfg = _cfg_with_two_resumes(data_root, user="r5")
    histories = [
        {"label": "Test Engineer", "slug": "test-engineer",
         "history": {"skills": ["selenium", "pytest", "automation"], "roles": []}},
        {"label": "QA Lead", "slug": "qa-lead",
         "history": {"skills": ["leadership", "roadmap"], "roles": []}},
    ]
    jp = JobPosting(source="usajobs", title="Automation Engineer", employer="Acme",
                    description="We need selenium and pytest automation experience.")
    best = discover.explain_best(jp, cfg, histories)
    assert best["resume"] == "Test Engineer"   # its skills match the posting -> higher score

    jp2 = JobPosting(source="usajobs", title="QA Manager", employer="Acme",
                     description="Own QA leadership and the testing roadmap.")
    assert discover.explain_best(jp2, cfg, histories)["resume"] == "QA Lead"


def test_explain_best_no_resume_still_ranks():
    jp = JobPosting(source="usajobs", title="Analyst", employer="Acme", description="data work")
    cfg = Config("x", {"search": {"titles": ["Analyst"]}})
    best = discover.explain_best(jp, cfg, [])
    assert best["resume"] == "" and "score" in best
