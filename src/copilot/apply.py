"""
Auto-apply — select the top matches, queue them for the executor, and log real submissions.
============================================================================================

DEPRECATED (`live` mode / the executor path): the Cowork+Chrome auto-fill/auto-submit flow this
module's `live` mode drove is dropped as the primary way to apply — too failure-prone and
desktop-bound. The current flow is the review dashboard (`web/dashboard.html` + `worker/`): rank
-> publish -> the user approves on the dashboard -> she gets emailed each pick's title +
application link and applies herself. `stage` mode (writing `applications/<week>/queue.md` for
your own reading) still works and is harmless; `live`/`record_results()` are kept for backward
compatibility only.

Flow (so you never hand-apply, while the ESD log stays truthful):

    rank (discover) -> select top N -> build_queue() -> applications/<week>/queue.(json|md)
                    -> [executor applies each posting]  -> applications/<week>/results.json
                    -> record_results() -> auto-log the REAL submissions to your ESD log

Modes (config `apply.mode`):
  - off   : do nothing.
  - stage : build the queue + briefs so you can review exactly what WOULD be submitted. No
            application is sent. (Recommended for your first run.)
  - live  : build the queue AND, when the executor has written results.json, log the real
            submissions. Turning this on means applications are actually sent — you can't un-apply.

The **executor** is Cowork (Claude driving the browser/desktop), which reads queue.md and applies
to each posting, adapting to each site, then writes results.json. That is the realistic way to
apply across arbitrary sites; a script can't reliably fill every ATS. Only submissions the
executor reports as `applied` are logged (truthful), and the weekly CLAIM is still certified by
you — this module never touches the claim.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import paths, logbook, discover
from .config import Config
from .draft import _cover_letter, _history
from .models import JobPosting

RESULT_APPLIED = "applied"
VALID_OUTCOMES = {"applied", "skipped", "failed", "needs_info"}


def select_top(postings: list[JobPosting], n: int) -> list[JobPosting]:
    return postings[:max(0, n)]


def _queue_item(jp: JobPosting, cover_letter: str, explanation: dict) -> dict:
    d = jp.to_dict()
    d["cover_letter"] = cover_letter
    d["score"] = explanation.get("score", 0)
    d["reasons"] = explanation.get("reasons", [])
    return d


def build_queue(cfg: Config, postings: list[JobPosting], week_end: date | None = None,
                data_root=None, top_n: int | None = None) -> Path:
    """Write applications/<week>/queue.json + queue.md (briefs the executor applies from)."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root
    n = top_n if top_n is not None else int(cfg.get("apply.top_n", 3))
    history = _history(cfg, data_root)
    resume_rel = cfg.get("profile.resume_path", "profile/resume.pdf")

    top = select_top(postings, n)
    # Explain with the SAME weekly-focus override discover used, so "Why ranked" matches the ranking.
    from . import focus as focus_mod
    focus = focus_mod.plan(cfg)
    eff = discover._with_focus(cfg, focus)
    explained = {jp.dedup_key: discover.explain(jp, eff, history, focus) for jp in top}
    items = [_queue_item(jp, _cover_letter(jp, cfg, history), explained[jp.dedup_key]) for jp in top]

    out_dir = paths.applications_dir(cfg.user, we, data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "queue.json").write_text(json.dumps({
        "week": we.isoformat(), "user": cfg.user, "resume_path": resume_rel,
        "mode": cfg.get("apply.mode", "stage"), "items": items,
    }, indent=2), encoding="utf-8")

    # Applicant info the executor needs to fill forms (from config `applicant`).
    a = cfg.get("applicant", {}) or {}
    links = a.get("links", {}) or {}
    applicant_lines = [
        "## Applicant info (use to fill application forms)",
        f"- Name: {a.get('full_name', cfg.get('display_name', cfg.user))}",
        f"- Email: {a.get('email', cfg.email_to)}",
        f"- Phone: {a.get('phone', '')}",
        f"- Location: {a.get('location', '')}",
        f"- Work authorization: {a.get('work_authorization', '')} "
        f"(requires sponsorship: {'yes' if a.get('requires_sponsorship') else 'no'})",
        f"- Willing to relocate: {'yes' if a.get('willing_to_relocate') else 'no'}",
        f"- Desired comp: {a.get('desired_comp', '') or '(prefer not to say unless required)'}",
        f"- Notice period: {a.get('notice_period', '')}",
        f"- LinkedIn: {links.get('linkedin', '')}   GitHub: {links.get('github', '')}   "
        f"Portfolio: {links.get('portfolio', '')}",
        f"- Résumé to upload: `{resume_rel}` (in this user's data folder)",
        "",
    ]

    # Human/Cowork-readable brief.
    md = [f"# Application queue — week ending {we.isoformat()} (user: {cfg.user})",
          f"\nMode: **{cfg.get('apply.mode', 'stage')}**. Résumé: `{resume_rel}`.\n",
          "Executor: apply to each posting below with the résumé + cover letter, filling forms from "
          "the applicant info. Record the outcome for each in `results.json` (see `results.template."
          "json`). Do NOT touch the weekly unemployment claim; only mark `applied` when a submission "
          "actually completed.\n",
          *applicant_lines]
    for i, jp in enumerate(top, 1):
        it = items[i - 1]
        md.append(f"## {i}. {jp.title}" + (f" @ {jp.employer}" if jp.employer else ""))
        md.append(f"- URL: {jp.url or '(none — search the employer site)'}")
        md.append(f"- Location: {jp.location or '(n/a)'}   Source: {jp.source}")
        md.append(f"- Why ranked (score {it['score']}): {'; '.join(it['reasons'])}")
        md.append("\n" + it["cover_letter"] + "\n")
    (out_dir / "queue.md").write_text("\n".join(md), encoding="utf-8")

    # A results template so the executor knows the exact shape to write back.
    tmpl = out_dir / "results.template.json"
    if not tmpl.exists():
        tmpl.write_text(json.dumps([{
            "dedup_key": jp.dedup_key, "employer": jp.employer, "title": jp.title,
            "url": jp.url, "outcome": "applied|skipped|failed|needs_info",
            "notes": "", "applied_at": we.isoformat(),
        } for jp in top], indent=2), encoding="utf-8")

    print(f"  [apply] queued top {len(top)} -> {out_dir/'queue.md'} (mode: {cfg.get('apply.mode','stage')})")
    return out_dir / "queue.json"


def record_results(cfg: Config, week_end: date | None = None, data_root=None) -> dict:
    """Read results.json from the executor and auto-log the REAL submissions. Idempotent."""
    we = week_end or paths.week_ending()
    data_root = data_root if data_root is not None else cfg.data_root
    results_path = paths.applications_dir(cfg.user, we, data_root) / "results.json"
    if not results_path.exists():
        return {"applied": 0, "logged": 0, "found": False}

    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  [apply] could not read results.json: {type(e).__name__}: {e}")
        return {"applied": 0, "logged": 0, "found": True, "error": str(e)}

    entries = []
    applied = 0
    for r in results if isinstance(results, list) else []:
        if str(r.get("outcome", "")).lower() != RESULT_APPLIED:
            continue
        applied += 1
        entries.append({
            "activity_type": "applied_to_job",
            "date": (r.get("applied_at") or we.isoformat())[:10],
            "employer_or_org": r.get("employer", ""),
            "position": r.get("title", ""),
            "contact_method": "online",
            "contact_name_or_url": r.get("url", ""),
            "result_status": "applied",
            "notes": (r.get("notes", "") + " [auto-applied]").strip(),
        })
    logged = logbook.append_confirmed(cfg.user, entries, data_root) if entries else 0
    print(f"  [apply] executor applied {applied}; logged {logged} new to your ESD log.")
    return {"applied": applied, "logged": logged, "found": True}


def run_apply(cfg: Config, postings: list[JobPosting], week_end: date | None = None,
              data_root=None) -> dict:
    """Orchestrate by mode. Returns a small summary dict."""
    mode = str(cfg.get("apply.mode", "stage")).lower()
    if mode == "off":
        print("  [apply] mode=off — skipping.")
        return {"mode": "off"}

    build_queue(cfg, postings, week_end, data_root)
    if mode == "stage":
        print("  [apply] STAGE — queued only; nothing submitted. Review queue.md, then set "
              "apply.mode: live to let the executor actually apply.")
        return {"mode": "stage"}

    # live
    summary = record_results(cfg, week_end, data_root)
    if not summary.get("found"):
        print("  [apply] LIVE — queue ready. Run your Cowork apply task to submit and write "
              "results.json, then re-run to log them.")
    return dict(summary, mode="live")
