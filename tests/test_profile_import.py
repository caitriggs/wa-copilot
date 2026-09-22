import io
import json
import zipfile

from copilot import config, paths, profile_import


def _write_config(user, data_root, extra_profile):
    paths.ensure_scaffold(user, data_root)
    text = (
        f"user: {user}\n"
        "search:\n  titles: ['Operations Manager']\n"
        "profile:\n" + "".join(f"  {k}: {v}\n" for k, v in extra_profile.items())
    )
    paths.config_path(user, data_root).write_text(text, encoding="utf-8")
    return config.load(user, data_root=data_root)


def _make_export_zip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = zipfile.ZipFile(path, "w")
    buf.writestr("Profile.csv", "First Name,Last Name,Headline,Summary\nCait,R,Operations Leader,Ran ops for a rental fleet.\n")
    buf.writestr("Positions.csv",
                 "Company Name,Title,Description,Started On,Finished On\n"
                 "Rad Rigs,Operations Manager,Managed logistics and fleet scheduling.,2022,2026\n")
    buf.writestr("Skills.csv", "Name\nLogistics\nFleet Management\nScheduling\n")
    buf.close()


def test_import_from_linkedin_export(data_root):
    cfg = _write_config("eu", data_root, {
        "linkedin_import": "export",
        "data_export_path": "profile/linkedin-export.zip",
    })
    _make_export_zip(paths.user_root("eu", data_root) / "profile" / "linkedin-export.zip")
    out = profile_import.import_history(cfg, data_root)
    hist = json.loads(out.read_text(encoding="utf-8"))
    assert hist["source"] == "export"
    assert hist["headline"] == "Operations Leader"
    assert any(r["title"] == "Operations Manager" and r["company"] == "Rad Rigs" for r in hist["roles"])
    assert "Logistics" in hist["skills"]
    assert hist["roles"][0]["bullets"]                      # description became a bullet


def test_import_from_resume_txt(data_root):
    cfg = _write_config("ru", data_root, {
        "linkedin_import": "resume",
        "resume_path": "profile/resume.txt",
    })
    resume = paths.user_root("ru", data_root) / "profile" / "resume.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text(
        "Jordan Lee — Operations Manager\n"
        "Seattle, WA\n\n"
        "Skills: Logistics, Fleet Management, Budgeting, Scheduling\n\n"
        "Experience\nRad Rigs — Operations Manager\n",
        encoding="utf-8",
    )
    out = profile_import.import_history(cfg, data_root)
    hist = json.loads(out.read_text(encoding="utf-8"))
    assert hist["source"] == "resume"
    assert "Operations Manager" in hist["headline"]
    assert "Logistics" in hist["skills"]
    assert "Budgeting" in hist["skills"]


def test_resume_headline_strips_name_and_extracts_highlights(data_root):
    cfg = _write_config("rh", data_root, {
        "linkedin_import": "resume", "resume_path": "profile/resume.txt"})
    r = paths.user_root("rh", data_root) / "profile" / "resume.txt"
    r.parent.mkdir(parents=True, exist_ok=True)
    r.write_text(
        "JORDAN LEE\nData Scientist | ML | NLP\nSeattle, WA\n\n"
        "Experience\n- Built an NLP pipeline that cut review time 40%\n- Led a team of 4 scientists\n",
        encoding="utf-8",
    )
    hist = json.loads(profile_import.import_history(cfg, data_root).read_text(encoding="utf-8"))
    assert hist["headline"] == "Data Scientist"          # name line skipped; title segment extracted
    assert any("NLP pipeline" in h for h in hist["highlights"])


def test_ensure_history_imports_when_missing(data_root):
    cfg = _write_config("mu", data_root, {
        "linkedin_import": "resume", "resume_path": "profile/resume.txt",
    })
    (paths.user_root("mu", data_root) / "profile" / "resume.txt").write_text(
        "Jane Doe — Program Manager\nSkills: Agile, Roadmapping\n", encoding="utf-8")
    hist = profile_import.ensure_history(cfg, data_root)
    assert "Agile" in hist["skills"]
    assert (paths.user_root("mu", data_root) / "profile" / "history.json").exists()


def test_core_skills_heading_with_category_labels(data_root):
    """Regression: a "CORE SKILLS" heading (not "Skills:"/"Core Competencies") with category-
    labeled, line-wrapped entries ("Tools: Jira, Confluence, ...") must not parse to 0 skills, and
    the category label itself shouldn't leak into the skill list. Mirrors real résumé formatting
    that previously collapsed multi-résumé badges to a single résumé (no scoring signal to
    differentiate them)."""
    cfg = _write_config("cs", data_root, {
        "linkedin_import": "resume", "resume_path": "profile/resume.txt",
    })
    (paths.user_root("cs", data_root) / "profile" / "resume.txt").write_text(
        "JANE DOE\n"
        "Project Manager\n"
        "Seattle, WA\n\n"
        "CORE SKILLS\n"
        "Project & Program Management: Project planning, scheduling, milestone tracking, risk\n"
        "identification and mitigation, release management\n"
        "Tools: Jira, Confluence, Microsoft Project\n"
        "PROFESSIONAL EXPERIENCE\n"
        "Acme Corp — Seattle, WA | 2020 – 2026\n",
        encoding="utf-8",
    )
    hist = json.loads(profile_import.import_history(cfg, data_root).read_text(encoding="utf-8"))
    assert len(hist["skills"]) > 5
    assert "Jira" in hist["skills"]
    assert "Confluence" in hist["skills"]
    assert "Project planning" in hist["skills"]
    # the category label shouldn't survive as its own "skill"
    assert "Project & Program Management" not in hist["skills"]
    assert "Tools" not in hist["skills"]
    # nothing from the next résumé section leaked in
    assert not any("Acme" in s or "PROFESSIONAL" in s for s in hist["skills"])


# --------------------------------------------------------------------------- LLM role extraction
# All tests here mock the Anthropic client — no real API key, no network call, ever.

class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error

    def create(self, **kwargs):
        if self._error:
            raise self._error
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text=None, error=None):
        self.messages = _FakeMessages(text=text, error=error)


def test_llm_roles_none_without_api(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: False)
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    assert profile_import._llm_roles(cfg, "some résumé text") is None


def test_llm_roles_none_for_empty_text(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    assert profile_import._llm_roles(cfg, "   ") is None


def test_llm_roles_parses_mocked_response(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text='[{"title": "Test Lead, Gameplay Systems", "company": "Bungie"}, '
                 '{"title": "Director of IT", "company": "Elevate"}]'
        ),
    )
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    roles = profile_import._llm_roles(cfg, "some résumé text")
    assert roles == [
        {"title": "Test Lead, Gameplay Systems", "company": "Bungie", "start": "", "end": "", "bullets": []},
        {"title": "Director of IT", "company": "Elevate", "start": "", "end": "", "bullets": []},
    ]


def test_llm_roles_strips_markdown_fences(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(text='```json\n[{"title": "QA Lead"}]\n```'),
    )
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    roles = profile_import._llm_roles(cfg, "some résumé text")
    assert roles[0]["title"] == "QA Lead"


def test_llm_roles_none_on_error(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(error=RuntimeError("simulated API failure")),
    )
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    assert profile_import._llm_roles(cfg, "some résumé text") is None


def test_llm_roles_none_on_malformed_json(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(text="not json at all"),
    )
    cfg = config.Config("u", {"search": {"titles": ["x"]}})
    assert profile_import._llm_roles(cfg, "some résumé text") is None


def test_import_histories_wires_roles_into_title_similarity(data_root, monkeypatch):
    """End-to-end: extracted roles land in history.json AND make discover.explain's
    title_similarity component actually differentiate a matching vs non-matching posting title —
    the exact signal that was missing when résumé-mode roles were always empty."""
    from copilot import discover
    from copilot.models import JobPosting

    monkeypatch.setattr(
        profile_import, "_llm_roles",
        lambda cfg, text: [{"title": "Program Manager", "company": "Acme",
                            "start": "", "end": "", "bullets": []}],
    )
    cfg = _write_config("wr", data_root, {
        "linkedin_import": "resume", "resume_path": "profile/resume.txt",
    })
    (paths.user_root("wr", data_root) / "profile" / "resume.txt").write_text(
        "Jane Doe — Program Manager\nCORE SKILLS\nAgile, Roadmapping\n", encoding="utf-8")

    results = profile_import.import_histories(cfg, data_root)
    assert results[0]["history"]["roles"] == [
        {"title": "Program Manager", "company": "Acme", "start": "", "end": "", "bullets": []}
    ]

    on_title = JobPosting(source="usajobs", title="Program Manager", employer="Cascade",
                          location="Seattle, WA", description="Lead program delivery.")
    off_title = JobPosting(source="usajobs", title="Staff Anesthesiologist", employer="VA",
                           location="Seattle, WA", description="Provide anesthesia care.")
    history = results[0]["history"]
    assert (discover.explain(on_title, cfg, history)["score"]
            > discover.explain(off_title, cfg, history)["score"])


def test_llm_roles_never_used_for_cover_letter_content():
    """De-risk regression: even if role extraction were wrong, it can't leak into a cover letter —
    llm.py's _profile_text() always prefers raw_text (résumé mode always sets it) over roles."""
    from copilot import llm
    history = {
        "raw_text": "Real résumé content that should be used verbatim.",
        "roles": [{"title": "FABRICATED TITLE", "company": "Fake Co", "start": "", "end": "", "bullets": []}],
        "skills": ["Jira"],
    }
    text = llm._profile_text(history)
    assert "FABRICATED TITLE" not in text
    assert "Real résumé content" in text


# --------------------------------------------------------------------------- generic-skill filter

def test_generic_bare_words_are_dropped():
    skills = ["Review", "Art", "Audio", "Design", "Animation", "Engineering", "risk", "Planning"]
    assert profile_import.filter_generic_skills(skills) == []


def test_real_single_word_skills_are_kept():
    """Tool/tech/domain names must never be treated as generic, even standalone."""
    skills = ["Jira", "Python", "SQL", "QA", "Selenium", "Confluence", "TFS"]
    assert profile_import.filter_generic_skills(skills) == skills


def test_compound_phrases_with_generic_words_are_kept():
    """A bare "planning" is junk; "sprint planning" or "Project planning" is a real skill —
    the filter only strips bare single-word generic terms, never multi-word phrases."""
    skills = ["sprint planning", "Project planning", "team leadership", "risk management"]
    assert profile_import.filter_generic_skills(skills) == skills


def test_soft_skills_commonly_self_declared_are_kept():
    """Words like leadership/management/communication/strategy are legitimately self-declared
    résumé skills ("Skills: Leadership, Communication, ...") and must not be blocked, even though
    they're also common in generic boilerplate — only the evidenced department/fragment words are."""
    skills = ["Leadership", "Management", "Communication", "Strategy", "Quality", "Training"]
    assert profile_import.filter_generic_skills(skills) == skills


def test_leading_conjunction_fragment_is_dropped():
    """A broken mid-phrase split ("...QA, and Services partnership") isn't a skill."""
    skills = ["and Services partnership", "Jira"]
    assert profile_import.filter_generic_skills(skills) == ["Jira"]


def test_department_list_fragments_dont_produce_false_skill_overlap():
    """Regression for the reported bug: an off-title posting whose boilerplate text happens to
    contain "review"/"Art" must not pick up a bogus skill_overlap match from a résumé's
    Cross-Discipline collaboration blurb."""
    from copilot import discover
    from copilot.models import JobPosting

    cfg = config.Config("u", {"search": {"titles": []}, "ranking": {}})
    history = {"skills": ["Review", "Art", "Jira"], "roles": []}
    jp = JobPosting(source="usajobs", title="Physician (Staff Anesthesiologist)", employer="VA",
                    description="Requires review of Art therapy programs.")
    result = discover.explain(jp, cfg, history)
    assert result["components"]["skill_overlap"] == 0


# --------------------------------------------------------------------------- title-family filter

def test_off_title_posting_is_filtered_out():
    from copilot import discover
    from copilot.models import JobPosting

    cfg = config.Config("u", {"search": {"titles": ["Project Manager", "QA Lead"]}, "ranking": {}})
    jp = JobPosting(source="usajobs", title="Physician (Staff Anesthesiologist)", employer="VA",
                    description="Provide anesthesia care.", comp_min=300000, comp_max=400000)
    assert discover.passes_filters(jp, cfg) is False


def test_on_title_family_posting_passes_without_exact_match():
    from copilot import discover
    from copilot.models import JobPosting

    cfg = config.Config("u", {"search": {"titles": ["Project Manager", "Technical Program Manager"]},
                              "ranking": {}})
    jp = JobPosting(source="usajobs", title="Program Manager", employer="Acme",
                    description="Lead program delivery.")
    assert discover.passes_filters(jp, cfg) is True


def test_title_filter_skipped_when_focus_relaxes_title():
    """A weekly focus deliberately wants stretch roles unlike past titles — must not be blocked."""
    from copilot import discover
    from copilot.models import JobPosting
    from copilot.focus import FocusPlan

    cfg = config.Config("u", {"search": {"titles": ["Project Manager"]}, "ranking": {}})
    jp = JobPosting(source="usajobs", title="Park Ranger", employer="State Parks",
                    description="Maintain outdoor trails.")
    focus = FocusPlan(queries=["Park Ranger"], keywords=["outdoor"], relax_title=True)
    assert discover.passes_filters(jp, cfg, focus) is True


def test_title_filter_skipped_when_no_titles_configured():
    from copilot import discover
    from copilot.models import JobPosting

    cfg = config.Config("u", {"search": {"titles": []}, "ranking": {}})
    jp = JobPosting(source="usajobs", title="Physician (Staff Anesthesiologist)", employer="VA",
                    description="Provide anesthesia care.")
    assert discover.passes_filters(jp, cfg) is True


def test_resume_text_reads_docx(tmp_path):
    """A Word .docx résumé is parsed with the stdlib (no pdfplumber, no extra deps)."""
    import zipfile
    from copilot.profile_import import _resume_text
    docx = tmp_path / "resume.docx"
    body = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
            '<w:p><w:r><w:t>MAX SAMPLE</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Product Manager</w:t></w:r></w:p>'
            '</w:body></w:document>')
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", body)
    text = _resume_text(docx)
    assert "MAX SAMPLE" in text and "Product Manager" in text
