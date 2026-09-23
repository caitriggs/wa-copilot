"""Cheap, pre-LLM location + relevance gate (discover.py) — narrows the fetched pool BEFORE either
(expensive) LLM stage ever runs. No network, no LLM — pure heuristics."""

from copilot import config, discover
from copilot.focus import FocusPlan
from copilot.models import JobPosting


def _cfg(locations=None, titles=None):
    return config.Config("u", {
        "search": {"titles": titles or ["Program Manager"],
                   "locations": locations if locations is not None else ["Seattle, WA", "Bellevue, WA"]},
        "ranking": {},
    })


def _posting(title="Program Manager", location="Seattle, WA", remote=False, description=""):
    return JobPosting(source="feeds", title=title, employer="Acme", location=location,
                      remote=remote, description=description)


# --------------------------------------------------------------------------- _passes_location


def test_location_in_configured_metro_is_kept():
    cfg = _cfg()
    jp = _posting(location="Seattle, WA - Downtown")
    assert discover._passes_location(jp, cfg) is True


def test_location_remote_is_kept_regardless_of_metro():
    cfg = _cfg()
    jp = _posting(location="Anywhere, USA", remote=True)
    assert discover._passes_location(jp, cfg) is True


def test_location_remote_string_without_flag_is_kept():
    """Tolerant of messy strings: some sources don't set remote=True but say so in the location
    text (e.g. "Remote" / "Anywhere" / "N/A")."""
    cfg = _cfg()
    jp = _posting(location="N/A", remote=False)
    assert discover._passes_location(jp, cfg) is True


def test_location_out_of_metro_is_dropped():
    cfg = _cfg()
    jp = _posting(location="New York, NY")
    assert discover._passes_location(jp, cfg) is False


def test_location_no_locations_configured_does_not_filter_blind():
    cfg = _cfg(locations=[])
    jp = _posting(location="New York, NY")
    assert discover._passes_location(jp, cfg) is True


# --------------------------------------------------------------------------- _relevance_pre_filter_ok


def test_relevance_lenient_under_relaxed_title_note():
    """A weekly focus note relaxes titles — a posting with ZERO title-family overlap must still
    survive on focus keyword/query overlap alone, not get hard-blocked by the title gate."""
    cfg = _cfg(titles=["Program Manager"])
    jp = _posting(title="Park Ranger", description="Maintain outdoor trails and lead crews.")
    focus = FocusPlan(queries=["Park Ranger"], keywords=["outdoor"], relax_title=True)
    assert discover._relevance_pre_filter_ok(jp, cfg, [], focus) is True


def test_relevance_kept_on_skill_overlap_alone():
    cfg = _cfg(titles=["Program Manager"])
    jp = _posting(title="Delivery Lead", description="Own the Jira backlog and sprint cadence.")
    histories = [{"label": "PM", "history": {"skills": ["Jira", "Scrum"], "roles": []}}]
    assert discover._relevance_pre_filter_ok(jp, cfg, histories, None) is True


def test_relevance_dropped_when_nothing_overlaps():
    cfg = _cfg(titles=["Program Manager"])
    jp = _posting(title="Dentist", description="Requires DDS license.")
    assert discover._relevance_pre_filter_ok(jp, cfg, [], None) is False


# --------------------------------------------------------------------------- _cheap_pre_filter


def test_cheap_pre_filter_drops_out_of_metro_off_discipline_noise():
    cfg = _cfg()
    postings = [
        _posting(title="Program Manager", location="Seattle, WA"),          # keep: metro + title
        _posting(title="Dentist", location="New York, NY"),                 # drop: neither
    ]
    kept, dropped = discover._cheap_pre_filter(postings, cfg, [], None)
    assert [jp.title for jp in kept] == ["Program Manager"]
    assert dropped == 1


def test_cheap_pre_filter_never_zeroes_out_the_pool():
    """If location+relevance would drop EVERY posting, the gate is skipped entirely rather than
    silently emptying the results before the LLM ever gets a look."""
    cfg = _cfg()
    postings = [
        _posting(title="Dentist", location="New York, NY"),
        _posting(title="Plumber", location="Miami, FL"),
    ]
    kept, dropped = discover._cheap_pre_filter(postings, cfg, [], None)
    assert kept == postings
    assert dropped == 0


def test_cheap_pre_filter_empty_input_short_circuits():
    cfg = _cfg()
    kept, dropped = discover._cheap_pre_filter([], cfg, [], None)
    assert kept == [] and dropped == 0


# --------------------------------------------------------------------------- titles_exclude


def _cfg_excl(terms):
    return config.Config("u", {"search": {"titles": ["Program Manager"], "locations": [],
                                          "titles_exclude": terms}, "ranking": {}})


def test_titles_exclude_drops_over_scoped_titles():
    cfg = _cfg_excl(["Senior Staff", "Director", "VP", "Office of the President"])
    for t in ("Senior Staff Operations Manager, Office of the President (PED)",
              "Director of Program Management", "VP, Product"):
        assert not discover.passes_hard_filters(_posting(title=t), cfg), t


def test_titles_exclude_is_whole_word_and_title_only():
    cfg = _cfg_excl(["VP", "Director"])
    # "VP" must not match inside another word; description mentions are harmless.
    assert discover.passes_hard_filters(_posting(title="MVP Program Manager"), cfg)
    assert discover.passes_hard_filters(
        _posting(title="Technical Program Manager", description="Reports to the Director of Eng."), cfg)


def test_titles_exclude_empty_by_default():
    assert discover.passes_hard_filters(_posting(title="Director of Programs"), _cfg())
