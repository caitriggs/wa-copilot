"""
Draft — write tailored application/cover-letter drafts for the top postings.
============================================================================

Writes one markdown draft per top-ranked posting into drafts/<week>/. These are DRAFTS ONLY —
nothing is submitted. You review, edit, and (if you choose) apply yourself, then log the ones you
actually applied to with scripts/log_activity.py.

Phase 1 uses a straightforward template seeded from your config + profile/history.json. Phase 2
can swap in richer, LLM-tailored drafts behind the same interface.
"""

from __future__ import annotations

import json
import re
from datetime import date

from . import paths
from .config import Config
from .models import JobPosting

REVIEW_BANNER = (
    "<!-- DRAFT ONLY — review, edit, and submit yourself. Nothing here has been sent. "
    "Log it only if you genuinely apply. -->"
)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "job").lower()).strip("-")[:60] or "job"


def _history(cfg: Config, data_root) -> dict:
    # Import history.json on first use if it isn't there yet.
    from .profile_import import ensure_history
    return ensure_history(cfg, data_root)


def _tokens(*parts) -> set:
    text = " ".join(p for p in parts if p).lower()
    return {t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 2}


def matched_skills(jp: JobPosting, history: dict) -> list[str]:
    """Skills from history that literally appear in the posting text."""
    from .profile_import import filter_generic_skills
    hay = f"{jp.title} {jp.employer} {jp.description}".lower()
    skills = filter_generic_skills(history.get("skills", []) or [])
    return [s for s in skills if s.lower() in hay][:8]


def best_role(jp: JobPosting, history: dict) -> dict | None:
    """The history role that best overlaps the posting (fallback: most recent = first)."""
    roles = history.get("roles", []) or []
    if not roles:
        return None
    want = _tokens(jp.title, jp.description)
    scored = sorted(
        roles,
        key=lambda r: len(_tokens(r.get("title", ""), " ".join(r.get("bullets", []))) & want),
        reverse=True,
    )
    return scored[0]


def _template_letter_body(jp: JobPosting, cfg: Config, history: dict) -> str:
    """The built-in (non-LLM) cover-letter body."""
    name = str(cfg.get("display_name", cfg.user))
    headline = (history.get("headline") or "").strip()
    notes = cfg.weekly_notes
    skills = matched_skills(jp, history) or (history.get("skills", []) or [])[:5]
    skills_str = ", ".join(skills)
    role = best_role(jp, history)

    at_emp = f" at {jp.employer}" if jp.employer else ""
    # Lead clause: prefer a real role title; else a clean headline; NEVER the person's name.
    if role and role.get("title"):
        lead = f"As a {role['title']}, "
    elif headline and headline.lower() != name.lower():
        lead = f"As {headline}, "
    else:
        lead = ""
    align = (f"{lead}my background aligns" if lead else "My background aligns")

    return (
        "Dear Hiring Manager,\n\n"
        f"I'm writing to apply for the {jp.title} role{at_emp}. "
        f"{align} with what you're looking for"
        f"{(', particularly ' + skills_str) if skills_str else ''}.\n\n"
        f"{('This week I am focused on ' + notes + '. ') if notes else ''}"
        "I'd welcome the chance to discuss how I can contribute.\n\n"
        f"Sincerely,\n{name}"
    )


def _cover_letter(jp: JobPosting, cfg: Config, history: dict) -> str:
    """Full draft document. Uses an LLM-written letter when configured; else the template."""
    skills = matched_skills(jp, history) or (history.get("skills", []) or [])[:5]
    skills_str = ", ".join(skills)

    ai_body = None
    try:
        from . import llm
        if llm.available(cfg):
            ai_body = llm.generate_cover_letter(cfg, jp, history)
    except Exception as e:  # noqa: BLE001 — never let drafting crash the run, but surface it
        print(f"  [llm] draft generation error: {type(e).__name__}: {str(e)[:200]}")
        ai_body = None

    if ai_body:
        heading = "## Cover letter (AI-drafted — verify every claim before sending)"
        body = ai_body
        achievements_block = ""
    else:
        heading = "## Cover letter (draft)"
        body = _template_letter_body(jp, cfg, history)
        role = best_role(jp, history)
        bullets = (role.get("bullets") if role else []) or history.get("highlights", []) or []
        ach = "".join(f"- {b}\n" for b in bullets[:3]) or \
            "- (add 1–3 concrete achievements that match this posting)\n"
        achievements_block = "## Relevant achievements (from your history — edit/verify)\n" + ach + "\n"

    return (
        f"{REVIEW_BANNER}\n\n"
        f"# Draft application — {jp.title}"
        + (f" @ {jp.employer}" if jp.employer else "") + "\n\n"
        f"- **Posting:** {jp.url or '(add URL)'}\n"
        f"- **Location:** {jp.location or '(n/a)'}\n"
        f"- **Source:** {jp.source}\n"
        f"- **Skills you have that this posting names:** {skills_str or '(none matched — add some)'}\n\n"
        f"{heading}\n\n"
        f"{body}\n\n"
        f"{achievements_block}"
        "## Tailoring checklist (do before sending)\n"
        "- [ ] Address it to a specific person/team if known\n"
        "- [ ] Verify every claim is true and matches your résumé\n"
        "- [ ] Mirror the posting's key requirements in your own words\n"
        "- [ ] Attach your current resume\n"
    )


def draft(cfg: Config, postings: list[JobPosting], week_end: date | None = None,
          data_root=None, top_n: int | None = None) -> list:
    """Write drafts for the top-N postings into drafts/<week>/. Returns the file paths."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root
    n = top_n if top_n is not None else int(cfg.get("ranking.top_n_drafts", 5))
    # Each posting is drafted against the résumé it best matches (multi-resume); single-resume
    # users get one history as before.
    from .profile_import import ensure_histories
    hs = ensure_histories(cfg, data_root)
    by_label = {h["label"]: h["history"] for h in hs}
    default_history = hs[0]["history"] if hs else {}

    out_dir = paths.drafts_dir(cfg.user, we, data_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tell the user which drafting mode is active — LLM or template — and why.
    from . import llm
    llm_ok, why = llm.status(cfg)
    if llm_ok:
        print(f"  [draft] LLM cover letters ON (model {cfg.get('draft.llm.model', 'claude-haiku-4-5')}).")
    else:
        print(f"  [draft] using the built-in template — LLM off: {why}")

    written, ai_count = [], 0
    for i, jp in enumerate(postings[:n], start=1):
        fname = f"{i:02d}-{_slug(jp.employer)}-{_slug(jp.title)}.md"
        path = out_dir / fname
        history = by_label.get(getattr(jp, "best_resume", "") or "", default_history)
        doc = _cover_letter(jp, cfg, history)
        if "AI-drafted" in doc:
            ai_count += 1
        path.write_text(doc, encoding="utf-8")
        written.append(path)

    print(f"  [draft] wrote {len(written)} draft(s) to {out_dir} (review before applying).")
    if llm_ok:
        if ai_count == len(written) and written:
            print(f"  [draft] all {ai_count} written by {cfg.get('draft.llm.model', 'claude-sonnet-5')}.")
        else:
            print(f"  [draft] WARNING: only {ai_count}/{len(written)} used the LLM — the rest fell "
                  "back to the template. The API call is failing; see any [llm] messages above.")
    return written
