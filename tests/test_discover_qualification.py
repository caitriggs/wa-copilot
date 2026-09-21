"""The single LLM qualification-rubric judge (discover.py) — screens cheap-pre-filter survivors
(see test_discover_prefilter.py) against ONE rubric that does both what used to be two separate
LLM passes: discipline/title relevance (dropping e.g. "Civil Engineer" for a "QA Engineer" résumé
despite the shared "Engineer" token) and the deeper qualification check — a posting's ACTUAL
requirements (specialized/domain experience, seniority, hard quals, skills), not just title
relevance. All tests mock the Anthropic client — no real API key, no network call."""

import json

from copilot import config, discover
from copilot.models import JobPosting


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


def _cfg():
    return config.Config("u", {
        "search": {"titles": ["Program Manager", "QA Lead"]},
        "profile": {"resumes": [
            {"label": "Project Manager", "path": "profile/pm.txt", "titles": ["Program Manager"]},
            {"label": "QA Lead", "path": "profile/qa.txt", "titles": ["QA Lead"]},
        ]},
        "ranking": {},
    })


def _histories():
    return [
        {"label": "Project Manager", "slug": "project-manager",
         "history": {"headline": "Technical Program Manager", "roles": [{"title": "Program Manager"}],
                     "skills": ["roadmapping", "stakeholder management"]}},
        {"label": "QA Lead", "slug": "qa-lead",
         "history": {"headline": "QA Lead", "roles": [{"title": "QA Lead"}],
                     "skills": ["test strategy", "automation"]}},
    ]


def _tribal_pm_posting():
    """The reported real-world example: title-matching Program Manager role whose actual
    Qualifications text requires domain experience the candidate's résumé doesn't show."""
    return JobPosting(
        source="usajobs", title="Program Manager, GS-0340-15", employer="Offices, Boards and Divisions",
        description="The Office on Violence Against Women leads national efforts.\n\n"
                    "Qualifications:\nApplicants must have at least 1 year of specialized experience "
                    "working with or in tribal communities on public safety issues or crimes of "
                    "violence against women under the Violence Against Women Act (VAWA).",
    )


def _ordinary_pm_posting():
    return JobPosting(source="usajobs", title="Program Manager", employer="Acme Corp",
                      description="Lead cross-functional delivery for our platform team.\n\n"
                                  "Qualifications:\n5+ years leading technical programs.")


def _civil_engineer_posting():
    """Bare token overlap trap: shares the word "Engineer" with a QA Engineer résumé but is a
    completely different discipline — this used to be caught by the separate stage-1 relevance
    judge; the merged rubric's dimension 1 (role/title/discipline fit) must catch it instead."""
    return JobPosting(source="usajobs", title="Civil Engineer", employer="City of Seattle",
                      description="Design roadways. Requires a PE license and a civil "
                                  "engineering degree.")


# --------------------------------------------------------------------------- _llm_qualification_batch


def test_qualification_batch_none_without_key(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: None)
    out = discover._llm_qualification_batch(cfg, "detail", "", [_ordinary_pm_posting()])
    assert out is None


def test_qualification_batch_keeps_qualified(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    jp = _ordinary_pm_posting()
    short_id = jp.dedup_key[:12]
    from copilot import llm
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text=json.dumps([{"id": short_id, "verdict": "qualified", "confidence": "high",
                              "reason": "matches PM background", "resume": "Project Manager"}])
        ),
    )
    out = discover._llm_qualification_batch(cfg, "detail", "", [jp])
    assert out[jp.dedup_key]["verdict"] == "qualified"
    assert out[jp.dedup_key]["resume"] == "Project Manager"


def test_qualification_batch_drops_specialized_experience_mismatch(monkeypatch):
    """The tribal-communities/VAWA example: title matches, but the specialized-experience
    requirement isn't evidenced by the résumé -> not-qualified."""
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    jp = _tribal_pm_posting()
    short_id = jp.dedup_key[:12]
    from copilot import llm
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text=json.dumps([{"id": short_id, "verdict": "not-qualified", "confidence": "high",
                              "reason": "requires tribal-communities/VAWA experience candidate lacks",
                              "resume": ""}])
        ),
    )
    out = discover._llm_qualification_batch(cfg, "detail", "", [jp])
    assert out[jp.dedup_key]["verdict"] == "not-qualified"
    assert "tribal" in out[jp.dedup_key]["reason"].lower() or "vawa" in out[jp.dedup_key]["reason"].lower()


def test_qualification_batch_drops_off_discipline_token_overlap_trap(monkeypatch):
    """Civil Engineer vs a résumé pool with no engineering discipline — bare 'engineer' token
    overlaps, but the merged rubric's dimension 1 (role/title/discipline fit) should drop it as a
    discipline mismatch, same as the old separate stage-1 relevance judge used to."""
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    jp = _civil_engineer_posting()
    short_id = jp.dedup_key[:12]
    from copilot import llm
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text=json.dumps([{"id": short_id, "verdict": "not-qualified", "confidence": "high",
                              "reason": "civil engineering discipline, not PM/QA", "resume": ""}])
        ),
    )
    out = discover._llm_qualification_batch(cfg, "detail", "", [jp])
    assert out[jp.dedup_key]["verdict"] == "not-qualified"


def test_qualification_batch_unrecognized_verdict_defaults_qualified(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    jp = _ordinary_pm_posting()
    short_id = jp.dedup_key[:12]
    from copilot import llm
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text=json.dumps([{"id": short_id, "verdict": "maybe??", "reason": "", "resume": ""}])
        ),
    )
    out = discover._llm_qualification_batch(cfg, "detail", "", [jp])
    assert out[jp.dedup_key]["verdict"] == "qualified"


def test_qualification_batch_none_on_api_error(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    from copilot import llm
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(error=RuntimeError("simulated API failure")),
    )
    out = discover._llm_qualification_batch(cfg, "detail", "", [_ordinary_pm_posting()])
    assert out is None


def test_qualification_batch_none_on_malformed_json(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    from copilot import llm
    monkeypatch.setattr(llm, "_make_client",
                        lambda anthropic_mod, api_key: _FakeClient(text="not json"))
    out = discover._llm_qualification_batch(cfg, "detail", "", [_ordinary_pm_posting()])
    assert out is None


# --------------------------------------------------------------------------- _llm_qualification_filter


def test_qualification_filter_fallback_when_no_key(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: False)
    cfg = _cfg()
    postings = [_ordinary_pm_posting(), _tribal_pm_posting()]
    kept, dropped, verdicts = discover._llm_qualification_filter(cfg, _histories(), "", postings)
    assert kept == postings
    assert dropped == 0
    assert verdicts == {}


def test_qualification_filter_drops_mismatch_keeps_qualified(monkeypatch):
    """One merged filter run must catch BOTH drop reasons the old two stages used to split
    between them — a clean off-discipline mismatch (civil engineer) and a domain/specialized-
    experience mismatch (tribal/VAWA) — while still keeping the genuinely qualified posting."""
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")

    ordinary = _ordinary_pm_posting()
    tribal = _tribal_pm_posting()
    civil = _civil_engineer_posting()

    def fake_create(**kwargs):
        body = json.loads(kwargs["messages"][0]["content"].split("# Postings to evaluate", 1)[1]
                          .split("\n", 1)[1])
        results = []
        for item in body:
            text = item["posting_text"].lower()
            if "tribal" in text or "vawa" in text:
                results.append({"id": item["id"], "verdict": "not-qualified", "confidence": "high",
                                "reason": "tribal-communities/VAWA experience not evidenced",
                                "resume": ""})
            elif "civil" in text or "roadways" in text:
                results.append({"id": item["id"], "verdict": "not-qualified", "confidence": "high",
                                "reason": "civil engineering discipline, not PM/QA", "resume": ""})
            else:
                results.append({"id": item["id"], "verdict": "qualified", "confidence": "high",
                                "reason": "matches PM background", "resume": "Project Manager"})
        return _FakeResponse(json.dumps(results))

    fake_messages = _FakeMessages()
    fake_messages.create = fake_create
    monkeypatch.setattr(llm, "_make_client",
                        lambda anthropic_mod, api_key: type("C", (), {"messages": fake_messages})())

    kept, dropped, verdicts = discover._llm_qualification_filter(
        cfg, _histories(), "", [ordinary, tribal, civil])
    assert kept == [ordinary]
    assert dropped == 2
    assert verdicts[ordinary.dedup_key]["verdict"] == "qualified"
    assert verdicts[ordinary.dedup_key]["resume"] == "Project Manager"
    assert tribal.dedup_key not in verdicts
    assert civil.dedup_key not in verdicts


def test_qualification_filter_rejects_hallucinated_resume_label(monkeypatch):
    """A résumé label the LLM invents (not one of the candidate's actual résumés) must not leak
    into the badge — it's cleared to empty so the caller falls back to the keyword-based pick."""
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    jp = _ordinary_pm_posting()
    short_id = jp.dedup_key[:12]
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(
            text=json.dumps([{"id": short_id, "verdict": "qualified", "confidence": "high",
                              "reason": "ok", "resume": "Senior Astronaut"}])
        ),
    )
    kept, dropped, verdicts = discover._llm_qualification_filter(cfg, _histories(), "", [jp])
    assert kept == [jp]
    assert verdicts[jp.dedup_key]["resume"] == ""


def test_qualification_filter_fails_open_on_batch_error(monkeypatch):
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")
    monkeypatch.setattr(
        llm, "_make_client",
        lambda anthropic_mod, api_key: _FakeClient(error=RuntimeError("boom")),
    )
    postings = [_ordinary_pm_posting(), _tribal_pm_posting()]
    kept, dropped, verdicts = discover._llm_qualification_filter(cfg, _histories(), "", postings)
    assert kept == postings
    assert dropped == 0
    assert verdicts == {}


def test_qualification_filter_empty_postings_short_circuits():
    cfg = _cfg()
    kept, dropped, verdicts = discover._llm_qualification_filter(cfg, _histories(), "", [])
    assert kept == [] and dropped == 0 and verdicts == {}


# --------------------------------------------------------------------------- concurrency


def test_qualification_concurrency_default():
    cfg = _cfg()
    assert discover._qualification_concurrency(cfg) == discover._QUALIFICATION_DEFAULT_CONCURRENCY


def test_qualification_concurrency_honors_config_override():
    cfg = config.Config("u", {"search": {"titles": []}, "ranking": {},
                              "discover": {"qualification_concurrency": 3}})
    assert discover._qualification_concurrency(cfg) == 3


def test_qualification_concurrency_invalid_value_falls_back_to_default():
    cfg = config.Config("u", {"search": {"titles": []}, "ranking": {},
                              "discover": {"qualification_concurrency": "not-a-number"}})
    assert discover._qualification_concurrency(cfg) == discover._QUALIFICATION_DEFAULT_CONCURRENCY


def test_qualification_concurrency_nonpositive_falls_back_to_default():
    cfg = config.Config("u", {"search": {"titles": []}, "ranking": {},
                              "discover": {"qualification_concurrency": 0}})
    assert discover._qualification_concurrency(cfg) == discover._QUALIFICATION_DEFAULT_CONCURRENCY


def test_qualification_filter_preserves_order_across_concurrent_batches(monkeypatch):
    """Batches now run concurrently (ThreadPoolExecutor) and can finish in ANY order. Make the
    first batch artificially slower than the second so they resolve out of submission order, and
    assert the final kept list + verdict mapping is nonetheless identical to the deterministic
    in-order result — posting->verdict identity must not depend on which API call lands first."""
    import time
    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    cfg = _cfg()
    monkeypatch.setattr(cfg, "get_secret", lambda ref: "sk-fake")

    # _QUALIFICATION_BATCH_SIZE is 6, so 8 postings split into a 6-posting batch (slow) and a
    # 2-posting batch (fast) — the fast one resolves first despite being submitted second.
    postings = [JobPosting(source="usajobs", title=f"Program Manager {i}", employer="Acme",
                           description=f"posting number {i}") for i in range(8)]

    def fake_create(**kwargs):
        text = kwargs["messages"][0]["content"]
        body = json.loads(text.split("# Postings to evaluate", 1)[1].split("\n", 1)[1])
        if len(body) > 2:
            time.sleep(0.1)   # the larger (first) batch finishes later
        return _FakeResponse(json.dumps(
            [{"id": item["id"], "verdict": "qualified", "confidence": "high",
              "reason": f"ok {item['id']}", "resume": "Project Manager"} for item in body]))

    fake_messages = _FakeMessages()
    fake_messages.create = fake_create
    monkeypatch.setattr(llm, "_make_client",
                        lambda anthropic_mod, api_key: type("C", (), {"messages": fake_messages})())

    kept, dropped, verdicts = discover._llm_qualification_filter(cfg, _histories(), "", postings)
    assert [jp.title for jp in kept] == [jp.title for jp in postings]
    assert dropped == 0
    for jp in postings:
        assert verdicts[jp.dedup_key]["reason"] == f"ok {jp.dedup_key[:12]}"


# --------------------------------------------------------------------------- end-to-end: discover()


def test_discover_merged_judge_drops_specialized_mismatch(monkeypatch, data_root):
    """End-to-end through discover(): the tribal PM survives the cheap pre-filter (title-relevant,
    matches search.titles) but the single merged LLM judge drops it outright for the specialized-
    experience mismatch — no separate stage-1 pass involved, and the wrong badge never survives."""
    from copilot import paths, profile_import
    user = "mergedu"
    root = paths.ensure_scaffold(user, data_root)
    (root / "profile").mkdir(parents=True, exist_ok=True)
    (root / "profile" / "pm.txt").write_text("Program Manager\nSkills: roadmapping\n", encoding="utf-8")
    (root / "profile" / "qa.txt").write_text("QA Lead\nSkills: test strategy\n", encoding="utf-8")
    paths.config_path(user, data_root).write_text(
        "user: mergedu\n"
        "search: { titles: ['Program Manager', 'QA Lead'] }\n"
        "profile:\n"
        "  resumes:\n"
        "    - { label: 'Project Manager', path: 'profile/pm.txt', titles: ['Program Manager'] }\n"
        "    - { label: 'QA Lead', path: 'profile/qa.txt', titles: ['QA Lead'] }\n",
        encoding="utf-8")
    cfg = config.load(user, data_root=data_root)

    monkeypatch.setattr(discover, "_load_histories", lambda cfg, dr=None: profile_import.ensure_histories(cfg, data_root))
    monkeypatch.setattr(discover, "SOURCE_FETCHERS", [lambda eff: [_tribal_pm_posting()]])

    from copilot import llm
    monkeypatch.setattr(llm, "has_api", lambda cfg: True)
    # Patch the module-level get_secret (not the cfg instance) — discover() builds a fresh `eff`
    # Config (via _with_resume_titles) distinct from `cfg`, and Config.get_secret delegates to
    # this free function regardless of which instance calls it.
    monkeypatch.setattr(config, "get_secret", lambda user, ref, data_root=None: "sk-fake")

    # Only one LLM call now — the merged qualification judge — and it drops the specialized-
    # experience mismatch directly.
    def fake_create(**kwargs):
        text = kwargs["messages"][0]["content"]
        body = json.loads(text.split("# Postings to evaluate", 1)[1].split("\n", 1)[1])
        return _FakeResponse(json.dumps(
            [{"id": i["id"], "verdict": "not-qualified", "confidence": "high",
              "reason": "tribal-communities/VAWA experience not evidenced", "resume": ""}
             for i in body]))

    fake_messages = _FakeMessages()
    fake_messages.create = fake_create
    monkeypatch.setattr(llm, "_make_client",
                        lambda anthropic_mod, api_key: type("C", (), {"messages": fake_messages})())

    results = discover.discover(cfg, data_root=data_root, write_cache=False)
    assert results == []   # the specialized-experience mismatch is dropped, not just re-badged
