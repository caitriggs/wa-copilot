from copilot import llm
from copilot.models import JobPosting

HISTORY = {
    "headline": "Data Scientist",
    "skills": ["NLP", "Machine Learning"],
    "roles": [{"title": "Data Scientist", "company": "Acme", "bullets": ["Built an NLP pipeline."]}],
    "raw_text": "JORDAN LEE\nData Scientist\n- Built an NLP pipeline that cut review time 40%\n",
}


def _posting():
    return JobPosting(source="adzuna", title="Senior Data Scientist", employer="Amazon",
                      location="Seattle, WA", description="NLP and ML at scale.")


def test_available_false_by_default(user_env):
    # draft.llm.enabled defaults to False
    assert llm.available(user_env) is False


def test_build_prompt_includes_no_fabrication_and_context(user_env):
    system, user = llm.build_prompt(user_env, _posting(), HISTORY)
    assert "do not invent" in system.lower() or "not invent" in system.lower()
    assert "only" in system.lower()                       # only facts from the profile
    assert "Senior Data Scientist" in user                # the posting
    assert "Amazon" in user
    assert "NLP pipeline" in user                          # the résumé facts
    assert "cover letter" in system.lower()


def test_generate_returns_none_without_key(user_env, monkeypatch):
    # enabled but no key -> None (caller falls back to template), no network attempted
    user_env.data["draft"]["llm"]["enabled"] = True
    monkeypatch.setattr(user_env, "get_secret", lambda ref: None)
    assert llm.generate_cover_letter(user_env, _posting(), HISTORY) is None
