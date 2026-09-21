"""
Discover — gather, dedup, filter, and rank postings from every enabled source.
==============================================================================

    discover(cfg) = fetch-all (per-source isolated) -> dedup -> comp/keyword filter
                    -> cheap location/relevance pre-filter -> LLM qualification judge -> rank

Ranking is a transparent weighted sum (weights in config `ranking`), so results are explainable:
comp-min fit + keyword hits + title similarity to your history + skill overlap + a boost for
terms in `search.weekly_notes`. `keywords_exclude` hard-drops a posting.

Before the (expensive) LLM judge runs, a cheap pre-filter (`_cheap_pre_filter`) narrows the pool
on two axes: LOCATION (`_passes_location` — keep only postings in the user's configured metro,
`search.locations`, or remote; usually the single biggest cut, since a statewide government RSS
feed spans far more cities than the user's target metro) and RELEVANCE (`_relevance_pre_filter_ok`
— keep a posting with SOME overlap against résumé skills, this week's focus keywords/queries, or
the title family; deliberately an OR of all three, not title alone, so a focus note that relaxes
titles doesn't get blocked by a bare title-token gate). Neither axis is discipline-aware ("Civil
Engineer" shares the token "engineer" with "QA Engineer") — that's still the LLM's job, and this
pre-filter is coarse by design: it bounds how many postings reach the LLM judge. If applying it
would drop the ENTIRE pool, it's skipped entirely and everything passes through unfiltered instead
(fail open — same philosophy as the LLM judge below), so a misconfigured location/keyword set can
never silently zero the results.

Postings that survive the pre-filter go to `_llm_qualification_filter`, ONE Haiku judge call (in
cost-conscious batches, run CONCURRENTLY via a bounded thread pool — config
`discover.qualification_concurrency`, default 8) that does what used to be two separate LLM passes:
it asks whether each
posting is genuinely relevant to at least one résumé's discipline (+ this week's focus note; not
just bare title-token overlap), screens for hard-requirement disqualifiers (degree/license/
clearance the candidate clearly lacks), AND checks whether the posting's ACTUAL requirements text
(title + description, which now includes the posting's Qualifications/specialized-experience
narrative — see sources/usajobs.py:_description) demands SPECIALIZED domain experience the
candidate's résumé doesn't evidence (e.g. a "Program Manager" role whose actual Qualifications
text demands experience with tribal communities / the Violence Against Women Act — title matches,
real requirements don't). It scores role/discipline fit, specialized/domain experience, seniority,
hard quals, and skills against the candidate's full résumé detail; drops genuine "not-qualified"
verdicts, keeps "stretch" roles, folds the verdict into the score, and sets the résumé badge to
the rubric's genuine best fit (not just whichever résumé happened to score highest on keywords).

The LLM judge fails OPEN on any problem (no key/SDK, API error) — the pre-filter's result stands,
nothing crashes, nothing is silently emptied. It never touches cover-letter content.

Results are cached to postings_cache/<week>.json (audit trail + dedup across runs).
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date

from . import paths
from .config import Config
from .models import JobPosting
from .sources import (usajobs, adzuna, jooble, careerjet, remotive, weworkremotely,
                      worksourcewa, feeds, email_alerts, authed)

SOURCE_FETCHERS = [usajobs.fetch, adzuna.fetch, jooble.fetch, careerjet.fetch,
                   remotive.fetch, weworkremotely.fetch, worksourcewa.fetch, feeds.fetch,
                   email_alerts.fetch, authed.fetch]

# Cheap/fast model for the single qualification judge only — never used for cover-letter content,
# which stays on whatever `draft.llm.model` the user configured (see llm.py).
_QUALIFICATION_MODEL = "claude-haiku-4-5"
_QUALIFICATION_BATCH_SIZE = 6   # smaller batch — sends fuller posting/résumé text per posting
# Batches run concurrently (ThreadPoolExecutor — each batch builds its own Anthropic client, so
# there's no shared mutable state to guard). Bounded so a large pool doesn't blow the API rate
# limit; override via config `discover.qualification_concurrency`.
_QUALIFICATION_DEFAULT_CONCURRENCY = 8
# Score multiplier applied per qualification verdict (see _llm_qualification_filter). A
# "not-qualified" verdict is dropped outright before this ever applies.
_QUALIFICATION_SCORE_MULT = {"qualified": 1.0, "stretch": 0.7}

# --- match-percent normalization + top-factor labeling ------------------------------------------
# match_pct is a SINGLE canonical 0-100 value computed once here and carried unchanged through
# postings_cache -> publish_week.py -> the dashboard Worker -> web/dashboard.html AND
# email_render.py, so the same job shows the same percentage on the board and in the email. It's
# each component's contribution as a fraction of the max that component could contribute given the
# ACTIVE (possibly focus-relaxed) weights for THIS posting — "how close to a perfect match under
# your configured weights" — not a comparison against other postings that happened to surface the
# same week (which would make the same job's % shift depending on what else was in the pool).
_FACTOR_LABELS = [  # (component key, display label) — list order breaks normalized-score ties
    ("title_similarity", "title match"),
    ("skill_overlap", "skills match"),
    ("keyword_hits", "keyword match"),
    ("comp_fit", "pay fit"),
    ("weekly_notes_boost", "this week's focus"),
    ("focus_match", "this week's focus"),
]

_QUALIFICATION_CONFIDENCE_NORM = {"high": 1.0, "medium": 0.7, "low": 0.4}


def _qualification_normalized(verdict: dict) -> float:
    """A 0-1 "how decisive was this" score for a qualification verdict, on the same scale as each
    explain() component's normalized (value/max) score — lets discover() decide whether
    "qualification fit" should win the top_factor label over e.g. title/skills."""
    conf = str(verdict.get("confidence", "")).strip().lower()
    base = _QUALIFICATION_CONFIDENCE_NORM.get(conf, 0.6)
    if verdict.get("verdict") == "qualified":
        return base
    if verdict.get("verdict") == "stretch":
        return base * 0.5
    return 0.0


def _load_history(cfg: Config, data_root=None) -> dict:
    # Auto-imports history.json from résumé/LinkedIn export on first use.
    from .profile_import import ensure_history
    return ensure_history(cfg, data_root if data_root is not None else cfg.data_root)


def _load_histories(cfg: Config, data_root=None) -> list[dict]:
    # One history per configured resume ([{label, slug, history}]); auto-imports on first use.
    from .profile_import import ensure_histories
    return ensure_histories(cfg, data_root if data_root is not None else cfg.data_root)


def _tokens(*parts) -> set[str]:
    text = " ".join(p for p in parts if p).lower()
    return {t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 2}


def _comp_fit(jp: JobPosting, comp_min: float | None) -> float:
    if comp_min is None:
        return 0.5
    # Best available pay figure for a floor comparison: the job's own comp_min (its guaranteed
    # low end) when known, since that's the defensible number to hold against a minimum
    # requirement; comp_max is only used as a fallback when the posting has no comp_min.
    val = jp.comp_min if jp.comp_min is not None else jp.comp_max
    if val is None:
        return 0.25  # unknown comp — mild neutral
    if val >= comp_min:
        return 1.0
    # Below the floor: taper by how far under (within 20% = partial credit).
    gap = (comp_min - val) / comp_min
    return max(0.0, 1.0 - gap * 5)  # 0 credit once >20% below the floor


def _with_focus(cfg: Config, focus) -> Config:
    """Return an effective Config where this week's focus OVERRIDES the usual search.

    When you've written a weekly note, we don't want the résumé/hard-title search anymore — we
    want the focus's search. So: replace search.titles with the focus queries (what the sources
    actually search for), fold the focus keywords into keywords_include, and — when the focus
    relaxes salary — drop the comp floor so plausible-stretch roles aren't filtered out. The
    original cfg is untouched; a deep copy is returned.
    """
    if focus is None:
        return cfg
    data = copy.deepcopy(cfg.data)
    search = data.setdefault("search", {})
    if focus.queries:
        search["titles"] = list(focus.queries)
    incl = list(search.get("keywords_include", []) or [])
    for k in focus.keywords:
        if k not in incl:
            incl.append(k)
    search["keywords_include"] = incl
    if focus.relax_comp:
        search["comp_min"] = None     # flex the salary floor for this week's focus
    return Config(cfg.user, data, data_root=cfg.data_root)


def _with_resume_titles(cfg: Config) -> Config:
    """Add each resume's associated `titles` to the search so both title families are fetched.

    Multi-resume users gear each résumé to a title family (e.g. "Test Engineer" vs "QA Lead");
    the union of those titles drives what the sources search for. Returns cfg unchanged when no
    resume contributes titles. (Skipped when a weekly focus is active — the focus overrides.)
    """
    extra = [t for r in cfg.resumes() for t in r.get("titles", [])]
    if not extra:
        return cfg
    data = copy.deepcopy(cfg.data)
    search = data.setdefault("search", {})
    titles = list(search.get("titles", []) or [])
    for t in extra:
        if t not in titles:
            titles.append(t)
    search["titles"] = titles
    return Config(cfg.user, data, data_root=cfg.data_root)


def _title_family_overlap(jp: JobPosting, cfg: Config) -> bool:
    """True if the posting's title shares at least one meaningful word with the union of the
    user's target titles (search.titles — by the time this runs it already includes every
    résumé's title family; see _with_resume_titles). A coarse relevance gate, not an exact match:
    "Program Manager" passes for someone targeting "Project Manager"/"Technical Program Manager"
    (shared tokens), but something with zero token overlap at all — e.g. "Physician (Staff
    Anesthesiologist)" for a PM/Product/QA job seeker — does not."""
    titles = cfg.get("search.titles", []) or []
    want: set[str] = set()
    for t in titles:
        want |= _tokens(t)
    if not want:
        return True  # nothing configured to compare against — don't filter blind
    return bool(_tokens(jp.title) & want)


_REMOTE_LOCATION_MARKERS = ("remote", "anywhere", "n/a", "work from home", "telework", "virtual")


def _is_remote_posting(jp: JobPosting) -> bool:
    """True if the posting is remote — either the source flagged it (remotive/weworkremotely
    always do) or its location string says so in one of the ways different sources phrase it."""
    if jp.remote:
        return True
    loc = (jp.location or "").strip().lower()
    return bool(loc) and any(marker in loc for marker in _REMOTE_LOCATION_MARKERS)


def _location_matches_metro(loc: str, wanted: list[str]) -> bool:
    """Tolerant of messy location strings across sources — matches by substring or bare city-name
    containment rather than requiring an exact string (e.g. "Seattle, WA - Downtown" matches a
    configured "Seattle, WA")."""
    loc_l = (loc or "").strip().lower()
    if not loc_l:
        return False
    for metro in wanted:
        m = (metro or "").strip().lower()
        if not m:
            continue
        if m in loc_l:
            return True
        city = m.split(",")[0].strip()
        if city and city in loc_l:
            return True
    return False


def _passes_location(jp: JobPosting, cfg: Config) -> bool:
    """Cheap pre-LLM location gate — keep only postings in the user's configured metro(s)
    (search.locations) or remote; drop everything else. Usually the single biggest cut against a
    large pool, since e.g. a statewide government RSS feed spans far more cities than the user's
    target metro."""
    if _is_remote_posting(jp):
        return True
    wanted = cfg.get("search.locations", []) or []
    if not wanted:
        return True  # nothing configured to filter against — don't filter blind
    return _location_matches_metro(jp.location, wanted)


def _relevance_pre_filter_ok(jp: JobPosting, cfg: Config, histories: list[dict], focus) -> bool:
    """Cheap pre-LLM relevance gate — keep a posting if it has SOME overlap with the candidate's
    résumé skills, this week's focus keywords/queries (when a note is active), or the title
    family (search.titles). Deliberately an OR across all three, not title alone: a focus note
    that relaxes titles must not get blocked by a bare title-token gate — a stretch role with zero
    title overlap still survives here on skill or focus-keyword overlap alone, and goes on to the
    (discipline-aware) LLM judge rather than being cut at this coarse stage."""
    if _title_family_overlap(jp, cfg):
        return True

    hay = _tokens(jp.title, jp.description)

    from .profile_import import filter_generic_skills
    for h in histories or []:
        skills = filter_generic_skills((h.get("history") or {}).get("skills", []) or [])
        if _tokens(" ".join(skills)) & hay:
            return True

    if focus is not None:
        terms = (list(focus.keywords) + list(focus.queries)
                + list(getattr(focus, "sectors", [])) + list(getattr(focus, "companies", [])))
        if _tokens(" ".join(t for t in terms if t)) & hay:
            return True

    return False


def _cheap_pre_filter(postings: list[JobPosting], cfg: Config, histories: list[dict],
                       focus) -> tuple[list[JobPosting], int]:
    """Apply the location + relevance gates to the full pool, in one pass, BEFORE either LLM
    stage — this is what actually bounds how many postings reach the (much more expensive) LLM
    judges. Never zeroes the pool: if the gate would drop every posting, it's skipped entirely and
    everything passes through unfiltered instead (fail open), so a misconfigured location or
    keyword set can never silently empty the results before the LLM ever gets a look."""
    if not postings:
        return postings, 0
    survivors = [jp for jp in postings
                if _passes_location(jp, cfg) and _relevance_pre_filter_ok(jp, cfg, histories, focus)]
    if not survivors:
        print(f"  [discover] cheap pre-filter would have dropped all {len(postings)} postings — "
              "skipping it, passing everything through to the LLM stages unfiltered instead.")
        return postings, 0
    return survivors, len(postings) - len(survivors)


def passes_hard_filters(jp: JobPosting, cfg: Config) -> bool:
    """Absolute excludes: configured keyword excludes and a comp floor blown by more than 20%.
    Unlike the cheap location/relevance pre-filter (`_cheap_pre_filter`), these are intentional
    hard drops — never relaxed by a never-zero-out fallback."""
    exclude = [k.lower() for k in (cfg.get("search.keywords_exclude", []) or [])]
    hay = f"{jp.title} {jp.employer} {jp.description}".lower()
    if any(k in hay for k in exclude):
        return False
    comp_min = cfg.get("search.comp_min")
    val = jp.comp_min if jp.comp_min is not None else jp.comp_max
    # Only hard-drop when comp is known AND entirely below the floor by a wide margin.
    if val is not None and comp_min is not None and val < comp_min * 0.8:
        return False
    return True


def passes_filters(jp: JobPosting, cfg: Config, focus=None,
                    histories: list[dict] | None = None) -> bool:
    """Single-posting convenience wrapper combining the hard excludes with the cheap
    location/relevance pre-filter (no never-zero-out fallback at this per-posting level — that
    fallback only makes sense pooled across all postings; see `_cheap_pre_filter`, used directly
    by `discover()`)."""
    if not passes_hard_filters(jp, cfg):
        return False
    if not _passes_location(jp, cfg):
        return False
    return _relevance_pre_filter_ok(jp, cfg, histories or [], focus)


def _resume_detail_brief(cfg: Config, histories: list[dict]) -> str:
    """Fuller per-résumé content (headline/summary/roles+bullets/skills/highlights, or raw résumé
    text when available) for the qualification rubric — judging discipline fit and whether a
    candidate's history actually EVIDENCES a posting's specialized/domain experience requirement
    needs more than a title+skills list; it needs the substance of what they actually did."""
    from . import llm
    parts = []
    for h in histories or []:
        label = h.get("label") or "Résumé"
        detail = llm._profile_text(h.get("history", {}) or {})[:1400]
        parts.append(f'### "{label}"\n{detail}' if detail else f'### "{label}"\n(no detail on file)')
    return "\n\n".join(parts) if parts else "(no résumé on file)"


_QUALIFICATION_SYSTEM = (
    "You are a careful hiring screener. For each job posting, assess whether THIS candidate is "
    "genuinely qualified, using their full résumé content (not just a title/skills list). Score "
    "across five dimensions, weighing 1 and 2 most heavily:\n"
    "1) Role/title/discipline fit — is the job's discipline/function genuinely what the candidate "
    "does? Judge discipline, not bare word overlap: \"Program Manager\" IS relevant to a \"Project "
    "Manager\" résumé; \"Civil Engineer\" or \"Nuclear Engineer\" is NOT relevant to a \"QA "
    "Engineer\" résumé even though both contain the word \"Engineer\". If a weekly focus note is "
    "given, let it further steer what counts as relevant this week.\n"
    "2) SPECIALIZED/DOMAIN experience — does the posting's text (including any Qualifications / "
    "specialized-experience narrative) require specific domain, industry, population, program, or "
    "subject-matter experience (e.g. \"experience working with tribal communities\", \"Violence "
    "Against Women Act\", \"HIPAA\", a named regulatory regime, a specific industry vertical) that "
    "the candidate's résumé does NOT evidence? Title match alone does not satisfy a stated "
    "specialized-experience requirement — the résumé must show it, not just be plausible for it. "
    "If the posting states a specific domain/population/subject-matter requirement the résumé "
    "gives no evidence of, treat that as a strong disqualifier even when the job title matches "
    "perfectly.\n"
    "3) Seniority/years/grade-level fit — does the candidate's experience level roughly match?\n"
    "4) Hard qualifications — a degree, professional license, clearance, or citizenship status the "
    "candidate clearly lacks per their résumé (e.g. a PE license, MD, JD, an active security "
    "clearance, a named engineering/medical/legal degree).\n"
    "5) Skills fit — overlap between the posting's stated requirements and the résumé's skills.\n"
    "Return ONLY minified JSON: a list of objects, one per posting, with keys \"id\" (echo the "
    "given id exactly), \"verdict\" (one of \"qualified\", \"stretch\", \"not-qualified\"), "
    "\"confidence\" (\"high\", \"medium\", or \"low\"), \"reason\" (under 20 words — the decisive "
    "factor, e.g. the discipline mismatch, the specific specialized-experience gap, or why it "
    "fits), and \"resume\" (the exact label of whichever candidate résumé is the genuine best fit "
    "for THIS posting, or \"\" if none is even a stretch). Be conservative with \"not-qualified\": "
    "use it only when you're confident — a genuine discipline mismatch, a real domain/specialized-"
    "experience gap, or a hard-requirement gap the résumé plainly doesn't meet, not a vague hunch. "
    "\"stretch\" is for plausible-but-not-obvious fits — keep those, don't mark them "
    "\"not-qualified\"."
)


def _llm_qualification_batch(cfg: Config, resume_detail: str, notes: str,
                              postings: list[JobPosting]) -> dict | None:
    """Ask Haiku to score each posting in this batch against the qualification rubric.

    Returns {dedup_key: {"verdict","confidence","reason","resume"}}, or None if the judge is
    unavailable for this batch (no SDK/key, or the API call/parse failed) — the caller fails OPEN
    (keeps the batch as "qualified", untouched) rather than dropping postings it couldn't judge.
    """
    from . import llm
    try:
        import anthropic
    except ImportError:
        return None
    api_key = cfg.get_secret("anthropic")
    if not api_key:
        return None

    by_id = {jp.dedup_key[:12]: jp for jp in postings}
    listing = [
        {"id": short_id, "title": jp.title, "employer": jp.employer,
         "posting_text": (jp.description or "")[:3000]}
        for short_id, jp in by_id.items()
    ]
    user = (
        f"# Candidate résumés (full detail)\n{resume_detail}\n\n"
        f"# This week's focus note (if any)\n{notes or '(none)'}\n\n"
        "# Postings to evaluate (title + full posting text, including qualifications/specialized "
        f"experience where the source provided it)\n{json.dumps(listing)}\n"
    )
    try:
        client = llm._make_client(anthropic, api_key)
        kwargs = dict(model=_QUALIFICATION_MODEL, max_tokens=1800, system=_QUALIFICATION_SYSTEM,
                      messages=[{"role": "user", "content": user}], timeout=60)
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001 — never let the qualification judge block discovery
        print(f"  [discover] LLM qualification judge failed for a batch of {len(postings)} "
              f"({type(e).__name__}: {str(e)[:200]}); keeping them (cheap pre-filter already applied).")
        return None

    out = {}
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        jp = by_id.get(str(item.get("id", "")))
        if jp is None:
            continue
        verdict = str(item.get("verdict", "qualified")).strip().lower()
        if verdict not in ("qualified", "stretch", "not-qualified"):
            verdict = "qualified"
        out[jp.dedup_key] = {
            "verdict": verdict,
            "confidence": str(item.get("confidence", ""))[:20],
            "reason": str(item.get("reason", ""))[:200],
            "resume": str(item.get("resume", ""))[:80],
        }
    return out


def _qualification_concurrency(cfg: Config) -> int:
    """Max concurrent qualification-judge batches — config `discover.qualification_concurrency`,
    default `_QUALIFICATION_DEFAULT_CONCURRENCY`. Any unparseable/non-positive override falls back
    to the default rather than erroring or silently running serially."""
    raw = cfg.get("discover.qualification_concurrency", _QUALIFICATION_DEFAULT_CONCURRENCY)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _QUALIFICATION_DEFAULT_CONCURRENCY
    return n if n > 0 else _QUALIFICATION_DEFAULT_CONCURRENCY


def _llm_qualification_filter(cfg: Config, histories: list[dict], notes: str,
                               postings: list[JobPosting]) -> tuple[list[JobPosting], int, dict]:
    """The single LLM screening pass: run the Haiku qualification-rubric judge over everything
    that survived the cheap location/relevance pre-filter (`_cheap_pre_filter`).

    One rubric does what used to be two separate LLM stages: discipline/title relevance (dropping
    e.g. "Civil Engineer" for a "QA Engineer" résumé despite the shared token) AND the deeper
    qualification check — the posting's ACTUAL requirements (specialized/domain experience,
    seniority, hard quals, skills) against the candidate's full résumé detail, not just title
    overlap. Drops genuine "not-qualified" verdicts (whether a clean discipline mismatch or a
    title-matching role whose stated specialized-experience requirement the résumé doesn't
    evidence); "stretch" roles are kept.

    Cost-conscious AND latency-conscious: split into batches of `_QUALIFICATION_BATCH_SIZE`, run
    concurrently (bounded by `_qualification_concurrency`, config `discover.
    qualification_concurrency`) via a thread pool — each batch makes its own independent API call
    with its own Anthropic client (`_llm_qualification_batch` builds a fresh one), so there's no
    shared mutable state across threads. Batches are indexed and reassembled by that index once
    all futures resolve, so the kept/verdicts output is IDENTICAL regardless of which batch's API
    call happens to return first — same posting→verdict mapping and ordering as the old serial
    loop. A batch that can't be judged (bad response, or a raised exception from the future itself)
    fails OPEN — its postings are kept, untouched, so neither a judge outage nor a threading hiccup
    in one batch can sink the whole run or silently empty the results. Returns
    (kept_postings, dropped_count, verdicts) where verdicts is
    {dedup_key: {"verdict","confidence","reason","resume"}} for postings that WERE judged (used by
    the caller to fold the verdict into ranking and set the résumé badge to the genuine best fit).
    """
    from . import llm
    if not postings or not llm.has_api(cfg):
        return postings, 0, {}

    resume_detail = _resume_detail_brief(cfg, histories)
    valid_labels = {h.get("label", "") for h in histories or [] if h.get("label")}

    batches = [postings[i:i + _QUALIFICATION_BATCH_SIZE]
              for i in range(0, len(postings), _QUALIFICATION_BATCH_SIZE)]
    judged_by_batch: list[dict | None] = [None] * len(batches)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    workers = min(_qualification_concurrency(cfg), len(batches))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(_llm_qualification_batch, cfg, resume_detail, notes, batch): idx
            for idx, batch in enumerate(batches)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                judged_by_batch[idx] = fut.result()
            except Exception as e:  # noqa: BLE001 — one batch's thread must never sink the run
                print(f"  [discover] LLM qualification judge batch {idx} raised in its thread "
                      f"({type(e).__name__}: {str(e)[:200]}); keeping it (fail open).")
                judged_by_batch[idx] = None

    kept: list[JobPosting] = []
    dropped = 0
    verdicts: dict = {}
    for batch, judged in zip(batches, judged_by_batch):
        if judged is None:
            kept.extend(batch)
            continue
        for jp in batch:
            v = judged.get(jp.dedup_key)
            if v is None:
                kept.append(jp)
                continue
            if v["resume"] and v["resume"] not in valid_labels:
                v["resume"] = ""   # don't let a hallucinated label leak into the résumé badge
            if v["verdict"] == "not-qualified":
                dropped += 1
                print(f"  [discover] dropped {jp.title!r} @ {jp.employer!r}: {v['reason']}")
                continue
            verdicts[jp.dedup_key] = v
            kept.append(jp)
    return kept, dropped, verdicts


def explain(jp: JobPosting, cfg: Config, history: dict, focus=None) -> dict:
    """Score a posting AND explain why: component contributions + human-readable reasons.

    Returns {"score", "components": {name: contribution}, "reasons": [str, ...]}.

    When a weekly `focus` (from focus.py) is passed, it OVERRIDES the usual ranking: a heavily
    weighted `focus_match` component drives the score toward the week's steer, title similarity is
    relaxed (so a plausible-stretch role isn't punished for a title you've never held), and comp
    fit goes neutral when the focus flexes salary. The résumé/skill signal still nudges — enough
    overlap to *plausibly* apply — but no longer decides.
    """
    w = cfg.get("ranking", {}) or {}
    comp_min = cfg.get("search.comp_min")
    incl = [k for k in (cfg.get("search.keywords_include", []) or [])]
    notes = cfg.weekly_notes

    hay = f"{jp.title} {jp.employer} {jp.description}".lower()
    kw_matched = [k for k in incl if k.lower() in hay]

    title_toks = _tokens(jp.title)
    hist_titles = " ".join(r.get("title", "") for r in history.get("roles", []))
    title_overlap = title_toks & _tokens(hist_titles)
    title_sim = len(title_overlap) / (len(title_toks) or 1)

    from .profile_import import filter_generic_skills
    skills = filter_generic_skills(history.get("skills", []) or [])
    skills_matched = [s for s in skills if s.lower() in hay]

    note_toks = _tokens(notes)
    note_overlap = note_toks & _tokens(jp.title, jp.description)

    comp_fit = _comp_fit(jp, comp_min)

    # --- focus overrides (a weekly note steers the whole week) ---
    relax_title = bool(focus and getattr(focus, "relax_title", False))
    relax_comp = bool(focus and getattr(focus, "relax_comp", False))
    focus_terms: list[str] = []
    focus_hits = 0
    if focus is not None:
        want = {t.lower() for t in (list(focus.keywords) + list(focus.queries)
                                    + list(getattr(focus, "sectors", []))
                                    + list(getattr(focus, "companies", [])))}
        want = {t for t in want if t}
        # A term "hits" if it (or a token of it) appears in the posting text.
        for term in want:
            toks = _tokens(term)
            if term in hay or (toks and toks <= _tokens(jp.title, jp.employer, jp.description)):
                focus_hits += 1
                focus_terms.append(term)

    title_weight = float(w.get("title_similarity", 2.0)) * (0.25 if relax_title else 1.0)
    comp_weight = 0.0 if relax_comp else float(w.get("comp_fit", 2.0))

    comp = {
        "comp_fit": round(comp_weight * comp_fit, 3),
        "keyword_hits": round(float(w.get("keyword_hits", 1.5)) * len(kw_matched), 3),
        "title_similarity": round(title_weight * title_sim, 3),
        "skill_overlap": round(float(w.get("skill_overlap", 1.5)) * min(len(skills_matched), 5), 3),
        "weekly_notes_boost": round(float(w.get("weekly_notes_boost", 1.0)) * min(len(note_overlap), 5), 3),
        "focus_match": round(float(w.get("focus_match", 3.5)) * min(focus_hits, 6), 3),
    }
    reasons = []
    if focus is not None and focus_terms:
        reasons.append(f"fits this week's focus: {', '.join(sorted(focus_terms)[:5])}")
    if skills_matched:
        reasons.append(f"matches your skills: {', '.join(skills_matched[:5])}")
    if kw_matched:
        reasons.append(f"hits your keywords: {', '.join(kw_matched[:5])}")
    if title_sim > 0 and not relax_title:
        reasons.append(f"title overlaps your past roles ({', '.join(sorted(title_overlap)[:4])})")
    elif title_sim > 0 and relax_title:
        reasons.append(f"some overlap with your past roles ({', '.join(sorted(title_overlap)[:4])}) — a plausible stretch")
    if relax_comp:
        reasons.append("salary floor relaxed for this week's focus")
    elif comp_fit >= 1.0:
        reasons.append("pay meets your target minimum")
    elif jp.comp_max or jp.comp_min:
        reasons.append("pay is near/below your target minimum")
    if focus is None and note_overlap:
        reasons.append(f"matches this week's focus: {', '.join(sorted(note_overlap)[:4])}")
    if not reasons:
        reasons.append("baseline match on your titles/locations")

    raw_score = sum(comp.values())

    # match_pct/top_factor use a SEPARATE view of comp_fit than the real ranking score above:
    # when no comp_min floor is configured, _comp_fit() always returns the neutral 0.5 — identical
    # for every posting, zero discriminating power. Counting it toward the percentage/top-factor
    # would either make it a spurious "top factor" (every posting "fully" earns a fixed neutral
    # credit) or permanently deflate every posting's percentage by an unfulfillable half-credit
    # ceiling. So it's excluded from the pct numerator AND denominator in that case — same
    # treatment as weekly_notes_boost/focus_match being excluded when that signal isn't in play
    # this week. The real ranking `score` above is untouched either way.
    pct_values = dict(comp)
    if comp_min is None:
        pct_values["comp_fit"] = 0.0

    # Max ACHIEVABLE contribution per component under THESE effective weights (title/comp already
    # reflect any focus relax) — the denominator for match_pct, and the basis for top_factor.
    # skill_overlap's cap of "5" is a soft ceiling on how many matches ever count (see `comp`
    # above) — it's not a claim the résumé HAS 5 skills, so the achievable max here is bounded by
    # however many (post-filter) skills the résumé actually lists, same reasoning as
    # keyword_hits being bounded by how many keywords are actually configured.
    maxes = {
        "comp_fit": (comp_weight * 1.0) if comp_min is not None else 0.0,
        "keyword_hits": float(w.get("keyword_hits", 1.5)) * len(incl),
        "title_similarity": title_weight * 1.0,
        "skill_overlap": float(w.get("skill_overlap", 1.5)) * min(5, len(skills)),
        "weekly_notes_boost": (float(w.get("weekly_notes_boost", 1.0)) * 5) if notes else 0.0,
        "focus_match": (float(w.get("focus_match", 3.5)) * 6) if focus is not None else 0.0,
    }
    pct_numerator = sum(pct_values.values())
    max_total = sum(maxes.values())
    match_pct = max(0, min(100, round(100 * pct_numerator / max_total))) if max_total > 0 else 0

    normalized = {k: (pct_values[k] / maxes[k] if maxes[k] > 0 else 0.0) for k in pct_values}
    top_key, top_norm = max(((k, normalized[k]) for k, _ in _FACTOR_LABELS), key=lambda kv: kv[1])
    top_factor = dict(_FACTOR_LABELS)[top_key]

    return {"score": round(raw_score, 4), "match_pct": match_pct,
            "top_factor": top_factor, "top_factor_score": round(top_norm, 4),
            "components": comp, "reasons": reasons}


def rank(jp: JobPosting, cfg: Config, history: dict, focus=None) -> float:
    return explain(jp, cfg, history, focus)["score"]


def explain_best(jp: JobPosting, cfg: Config, histories: list[dict], focus=None) -> dict:
    """Score jp against EVERY resume history and return the best result, tagged with its resume.

    This is what keeps it ONE ranked pool rather than separate per-resume pools: a posting's score
    is its best score across résumés, and `resume`/`resume_slug` name which résumé won — that's the
    badge shown next to the match in the email and dashboard.
    """
    best = None
    for h in histories or []:
        r = explain(jp, cfg, h.get("history", {}) or {}, focus)
        r["resume"], r["resume_slug"] = h.get("label", ""), h.get("slug", "")
        if best is None or r["score"] > best["score"]:
            best = r
    if best is None:                       # no résumé configured — still rank, just unbadged
        best = explain(jp, cfg, {}, focus)
        best["resume"], best["resume_slug"] = "", ""
    return best


def discover(cfg: Config, week_end: date | None = None, data_root=None,
             write_cache: bool = True) -> list[JobPosting]:
    """Return postings ranked best-first. Per-source failures are isolated."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root
    histories = _load_histories(cfg, data_root)

    # A weekly note OVERRIDES the usual search: it drives what we fetch/filter (eff cfg) and how we
    # rank (focus passed to explain). No note -> focus is None; the search is instead widened by
    # each résumé's title family so a multi-résumé user's whole pool is fetched.
    from . import focus as focus_mod
    focus = focus_mod.plan(cfg)
    eff = _with_focus(cfg, focus) if focus is not None else _with_resume_titles(cfg)

    collected: dict[str, JobPosting] = {}
    for fetch in SOURCE_FETCHERS:
        try:
            for jp in fetch(eff):
                collected.setdefault(jp.dedup_key, jp)  # first source wins on dupes
        except Exception as e:  # noqa: BLE001
            print(f"  [discover] source {getattr(fetch, '__module__', '?')} errored: "
                  f"{type(e).__name__}: {e}")

    screened = len(collected)
    hard_filtered = [jp for jp in collected.values() if passes_hard_filters(jp, eff)]
    pre_filtered, cheap_dropped = _cheap_pre_filter(hard_filtered, eff, histories, focus)
    heuristic_dropped = screened - len(pre_filtered)
    print(f"  [discover] cheap pre-filter (location+relevance): kept {len(pre_filtered)}/"
          f"{len(hard_filtered)} ({cheap_dropped} dropped before the LLM stage; "
          f"{screened - len(hard_filtered)} more dropped by keyword-exclude/comp floor).")
    # Coarse pre-filter already ran (location + relevance, above). The single LLM qualification
    # pass re-screens survivors on one rubric: genuine title/discipline relevance + hard-
    # requirement disqualifiers + the deeper qualification check (specialized/domain experience,
    # seniority, hard quals, skills) against the posting's ACTUAL requirements text. Fails open on
    # any problem.
    kept, llm_dropped, qual_verdicts = _llm_qualification_filter(eff, histories, cfg.weekly_notes, pre_filtered)

    # One pool: each posting scored against all résumés, best score wins, tagged with its résumé —
    # then the verdict (if any) adjusts the score and can override the badge with the rubric's
    # genuine best-fit résumé (fixes a title-driven mis-badge like QA Lead on a PM role).
    explained = {jp.dedup_key: explain_best(jp, eff, histories, focus) for jp in kept}
    for jp in kept:
        entry = explained[jp.dedup_key]
        verdict = qual_verdicts.get(jp.dedup_key)
        if verdict:
            mult = _QUALIFICATION_SCORE_MULT.get(verdict["verdict"], 1.0)
            entry["score"] = round(entry["score"] * mult, 4)
            entry["match_pct"] = max(0, min(100, round(entry.get("match_pct", 0) * mult)))
            qual_norm = _qualification_normalized(verdict)
            if qual_norm >= entry.get("top_factor_score", 0.0):
                entry["top_factor"] = "qualification fit"
            if verdict.get("reason"):
                entry["reasons"] = [f"qualification check: {verdict['reason']}"] + entry["reasons"]
            if verdict.get("resume"):
                entry["resume"] = verdict["resume"]
        jp.best_resume = entry.get("resume", "")
    kept.sort(key=lambda jp: explained[jp.dedup_key]["score"], reverse=True)

    if write_cache:
        cache = paths.postings_cache_path(cfg.user, we, data_root)
        cache.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "week": we.isoformat(),
            "focus": (focus.summary if focus else ""),   # the week's steer, if any
            "screened": screened,                 # total discovered before filtering
            "filtered_out": screened - len(kept),
            "heuristic_filtered_out": heuristic_dropped,       # dropped by the coarse location/relevance/comp/keyword gate
            "llm_filtered_out": llm_dropped,                   # dropped by the single LLM qualification judge
            "ranked": len(kept),
            "postings": [dict(jp.to_dict(), score=explained[jp.dedup_key]["score"],
                              match_pct=explained[jp.dedup_key]["match_pct"],
                              top_factor=explained[jp.dedup_key]["top_factor"],
                              reasons=explained[jp.dedup_key]["reasons"],
                              components=explained[jp.dedup_key]["components"],
                              best_resume=explained[jp.dedup_key].get("resume", "")) for jp in kept],
        }
        cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"  [discover] screened {screened}, ranked {len(kept)} after filtering "
          f"({heuristic_dropped} by the cheap pre-filter, {llm_dropped} by the LLM qualification judge).")
    return kept
