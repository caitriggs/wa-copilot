"""usajobs._description: JobSummary + QualificationSummary must both survive (concatenated, not
either-or) — the specialized-experience narrative that the LLM qualification screen needs
(discover.py) lives in QualificationSummary, distinct text from the JobSummary blurb."""

from copilot.sources import usajobs


def _descriptor(job_summary="", qualification_summary=""):
    return {
        "UserArea": {"Details": {"JobSummary": job_summary}},
        "QualificationSummary": qualification_summary,
    }


def test_description_concatenates_both_when_present():
    d = _descriptor(
        job_summary="The Office on Violence Against Women leads national efforts.",
        qualification_summary="Applicants must have 1 year of specialized experience working "
                              "with tribal communities on public safety programs.",
    )
    out = usajobs._description(d)
    assert "Office on Violence Against Women" in out
    assert "tribal communities" in out
    assert "Qualifications:" in out


def test_description_falls_back_to_job_summary_only():
    d = _descriptor(job_summary="Lead the platform QA team.", qualification_summary="")
    out = usajobs._description(d)
    assert out == "Lead the platform QA team."


def test_description_falls_back_to_qualifications_only():
    d = _descriptor(job_summary="", qualification_summary="Must have a PE license.")
    out = usajobs._description(d)
    assert "PE license" in out
    assert "Qualifications:" in out


def test_description_empty_when_both_missing():
    assert usajobs._description(_descriptor()) == ""


def test_parse_wires_description_into_job_posting():
    mi = {"MatchedObjectDescriptor": {
        "PositionTitle": "Program Manager", "OrganizationName": "DOJ",
        "UserArea": {"Details": {"JobSummary": "Runs OVW programs."}},
        "QualificationSummary": "Specialized experience with tribal communities and VAWA required.",
    }}
    jp = usajobs._parse(mi, [])
    assert "Runs OVW programs." in jp.description
    assert "tribal communities" in jp.description
