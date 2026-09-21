from copilot import draft
from copilot.models import JobPosting

HISTORY = {
    "headline": "Operations Leader",
    "skills": ["Logistics", "Fleet Management", "Budgeting"],
    "roles": [
        {"title": "Warehouse Lead", "company": "OldCo", "bullets": ["Ran a dock crew."]},
        {"title": "Operations Manager", "company": "Rad Rigs",
         "bullets": ["Cut fleet logistics costs 18%.", "Owned scheduling."]},
    ],
}


def _posting():
    return JobPosting(
        source="usajobs", title="Operations Manager", employer="Cascade",
        location="Seattle, WA",
        description="Seeking an operations manager with logistics and fleet management experience.",
    )


def test_matched_skills_picks_posting_terms():
    ms = draft.matched_skills(_posting(), HISTORY)
    assert "Logistics" in ms
    assert "Fleet Management" in ms
    assert "Budgeting" not in ms                 # not named in the posting


def test_best_role_prefers_overlap():
    role = draft.best_role(_posting(), HISTORY)
    assert role["title"] == "Operations Manager"  # overlaps posting more than Warehouse Lead


def test_cover_letter_uses_history(user_env, tmp_path):
    text = draft._cover_letter(_posting(), user_env, HISTORY)
    assert "Logistics" in text
    assert "Cut fleet logistics costs 18%." in text   # a real achievement bullet
    assert "DRAFT ONLY" in text


def test_cover_letter_wires_llm_with_correct_args(user_env, monkeypatch):
    """Regression: draft must call llm.generate_cover_letter(cfg, jp, ...) — not (jp, cfg, ...)."""
    from copilot import llm
    seen = {}

    def fake_generate(cfg, jp, history):
        seen["cfg_has_user"] = hasattr(cfg, "user")     # cfg first, jp second
        seen["jp_has_dedup"] = hasattr(jp, "dedup_key")
        return "This is a real, human-sounding letter."

    monkeypatch.setattr(llm, "available", lambda cfg: True)
    monkeypatch.setattr(llm, "generate_cover_letter", fake_generate)
    doc = draft._cover_letter(_posting(), user_env, HISTORY)
    assert seen == {"cfg_has_user": True, "jp_has_dedup": True}
    assert "AI-drafted" in doc
    assert "This is a real, human-sounding letter." in doc


def test_draft_writes_files(user_env, data_root):
    postings = [_posting()]
    paths_written = draft.draft(user_env, postings, data_root=data_root, top_n=1)
    assert len(paths_written) == 1
    assert paths_written[0].exists()
    assert paths_written[0].read_text(encoding="utf-8").startswith("<!-- DRAFT ONLY")
