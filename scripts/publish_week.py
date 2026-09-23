"""
Publish a week's staged picks from wa-unemployment-copilot to the review dashboard Worker.
============================================================================================

SETUP: run from wherever the weekly wa-unemployment-copilot pipeline runs (RADMACHINE), from a
checkout of this repo (needed for the cover-letter generation step below). The publish/network
path itself needs no dependencies beyond the stdlib (urllib).

READS (from that user's local data folder, never from this repo):
    <Desktop>/wa-unemployment-copilot/<user>/postings_cache/<week>.json   (this week's ranked jobs)
    <Desktop>/wa-unemployment-copilot/<user>/applications/<week>/queue.json  (drafted letters, if any)
    <Desktop>/wa-unemployment-copilot/<user>/log/job_search_log.csv

COVER LETTERS: the top 5 postings by match_score get a tailored cover letter attached, generated
with the same generator apply.py uses to build the apply queue (copilot.draft._cover_letter —
LLM-tailored when the user has `draft.llm.enabled: true` + an API key, else the built-in
template). A letter already drafted for the apply queue (queue.json) is reused instead of
regenerated. Generation runs against THIS repo's `src/copilot` package (imported lazily, only
when a letter is actually missing) so it has access to the user's profile/config/secrets; if that
import or a specific posting's generation fails, it's logged and skipped — the rest of publish
proceeds and that job is published without a letter. Postings outside the top 5 are published
(for the dashboard's "show all" toggle) without a generated letter.

BUILDS: {week, jobs: [...], metrics: {...}, applicant: {...}, resumes: {label: filename}} and
PUTs it to the dashboard Worker's `PUT /api/week`, authenticated with a bearer token read from
the environment (WA_COPILOT_PUBLISH_TOKEN) — never pass the token on the command line and never
print it. `applicant` (from config `applicant`) and `resumes` (résumé label -> PDF filename, from
config `profile.resumes`) let the Worker build the submissions CSV + Claude CoWork prompt on
demand from KV — the dashboard's "get my stuff" buttons — instead of that only existing in the
RADMACHINE-side apply email. Both degrade gracefully: a week published before this change (or a
config that can't load) simply omits them, and the Worker treats them as empty.
If the Worker route is also fronted by Cloudflare Access, set WA_COPILOT_CF_ACCESS_CLIENT_ID
and WA_COPILOT_CF_ACCESS_CLIENT_SECRET (a Cloudflare Access service token) so requests carry
the CF-Access-Client-Id/CF-Access-Client-Secret headers alongside the bearer token; both are
optional and, if unset, the script behaves exactly as before (bearer token only).

USAGE:
    python publish_week.py --user demo --week 2026-08-01 --worker-url https://wa-copilot-dashboard.<subdomain>.workers.dev
    python publish_week.py --user demo --week 2026-08-01 --dry-run   # print payload, no network call
    # dev/test: publishes the week as usual, but the review email goes to the dev recipient with a
    # ?dev=1 dashboard link (a Submit from it is NOT recorded in KV):
    python publish_week.py --user demo --dev-test --dev-to demo@gmail.com

EXIT CODES: 0 ok, 1 missing/unreadable input files, 2 network/HTTP error, 3 bad arguments.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

APP_DIRNAME = "wa-unemployment-copilot"
TOP_N_LETTERS = 5  # top-ranked postings (by match_score) that get a generated cover letter


def desktop_dir() -> Path:
    """Resolve the current user's Desktop (Windows classic or OneDrive-redirected).

    Kept in sync with wa-unemployment-copilot/src/copilot/paths.py:desktop_dir(). This script
    intentionally doesn't import that package so it has no dependency on the copilot repo being
    on PYTHONPATH — RADMACHINE may run this from a different working directory/venv.
    """
    override = os.environ.get("WA_UI_DESKTOP")
    if override:
        return Path(override)
    home = Path(os.path.expanduser("~"))
    candidates = []
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        candidates.append(Path(onedrive) / "Desktop")
    candidates.append(home / "OneDrive" / "Desktop")
    candidates.append(home / "Desktop")
    for c in candidates:
        if c.exists():
            return c
    return home / "Desktop"


def week_ending(d: date | None = None) -> date:
    """The Saturday that ends the Sun..Sat claim week containing `d` (default: today)."""
    d = d or date.today()
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        delta = 0
    elif wd == 6:
        delta = 6
    else:
        delta = 5 - wd
    return d + timedelta(days=delta)


def user_root(user: str, data_root: str | None = None) -> Path:
    base = Path(data_root) if data_root else desktop_dir() / APP_DIRNAME
    return base / user


def _fmt_comp(item: dict) -> str:
    lo, hi = item.get("comp_min"), item.get("comp_max")
    source = item.get("comp_source") or ""
    if lo and hi:
        s = f"${lo:,.0f}–${hi:,.0f}"
    elif lo or hi:
        s = f"${(lo or hi):,.0f}"
    else:
        return "n/a"
    if source == "estimate":
        s += " (est.)"
    return s


def _job_from_posting(item: dict, cover_letter: str = "") -> dict:
    return {
        "id": item.get("dedup_key", ""),
        "title": item.get("title", ""),
        "org": item.get("employer", ""),
        "location": item.get("location", ""),
        "comp": _fmt_comp(item),
        "match_score": item.get("score", 0),      # raw weighted sum — internal ranking/sort only
        # match_pct/top_factor are computed ONCE in discover.py (explain()) and carried through
        # unchanged from here into the Worker payload and the review email, so the SAME job shows
        # the SAME 0-100 % and "surfaced for" label on the dashboard and in the email.
        "match_pct": item.get("match_pct", 0),
        "top_factor": item.get("top_factor", ""),
        "why_matched": "; ".join(item.get("reasons", [])),
        "url": item.get("url", ""),
        "cover_letter": cover_letter,
        # Extra fields (harmless to the dashboard, which ignores unknown keys) used to render the
        # weekly-review email cards: posting age, work mode, a short blurb, and the ranked reasons
        # (strongest first) so the email can show WHY a role scored the way it did.
        "posted_date": item.get("posted_date", ""),
        "remote": bool(item.get("remote", False)),
        "description": item.get("description", ""),
        "reasons": list(item.get("reasons", []) or []),
        "resume": item.get("best_resume", ""),   # résumé this posting best matches (badge)
    }


def load_ranked_postings(user_dir: Path, week: str) -> list[dict]:
    """This week's postings, best-first, from discover's cache (score/reasons already computed)."""
    cache_path = user_dir / "postings_cache" / f"{week}.json"
    if not cache_path.exists():
        return []
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return cache.get("postings", []) or []


def load_queue(user_dir: Path, week: str) -> dict:
    queue_path = user_dir / "applications" / week / "queue.json"
    if not queue_path.exists():
        return {}
    try:
        return json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def queue_letters(queue: dict) -> dict[str, str]:
    """dedup_key -> already-drafted cover letter from the apply queue (reuse; don't regenerate)."""
    out = {}
    for item in queue.get("items", []) or []:
        key, letter = item.get("dedup_key", ""), item.get("cover_letter", "")
        if key and letter:
            out[key] = letter
    return out


def _import_cover_letter_generator():
    """Best-effort import of this repo's copilot package, for cover-letter generation only.

    Confined to here (rather than a top-of-file import) so a missing/broken copilot install
    degrades to "no letters generated" instead of blocking the rest of publish — the
    publish/network path has no dependency on this package.
    """
    repo_src = Path(__file__).resolve().parent.parent / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from copilot.config import load as load_config
    from copilot.draft import _cover_letter
    from copilot.profile_import import ensure_histories
    from copilot.models import JobPosting
    return load_config, _cover_letter, ensure_histories, JobPosting


def generate_top_letters(user: str, postings: list[dict], existing: dict[str, str],
                         data_root: str | None, top_n: int = TOP_N_LETTERS) -> tuple[dict[str, str], int]:
    """Cover letters (dedup_key -> letter) for the top-N postings by match_score.

    Reuses an already-drafted queue.json letter when present; generates the rest with the same
    generator apply.py uses (LLM-tailored when configured, else the built-in template). One
    posting's failure is logged and skipped, never aborting the rest of publish.
    """
    ranked = sorted(postings, key=lambda p: p.get("score", 0), reverse=True)[:max(0, top_n)]
    letters = dict(existing)
    to_generate = [p for p in ranked if not letters.get(p.get("dedup_key", ""))]
    if not to_generate:
        return letters, 0

    try:
        load_config, cover_letter_fn, ensure_histories_fn, JobPosting = _import_cover_letter_generator()
        cfg = load_config(user, data_root=data_root)
        hs = ensure_histories_fn(cfg, data_root)
        by_label = {h["label"]: h["history"] for h in hs}
        default_history = hs[0]["history"] if hs else {}
    except Exception as e:  # noqa: BLE001
        print(f"  [publish] cover-letter generation unavailable ({type(e).__name__}: {e}); "
              f"{len(to_generate)} top-ranked job(s) will publish without a letter.")
        return letters, 0

    generated = 0
    for item in to_generate:
        key = item.get("dedup_key", "")
        try:
            jp = JobPosting.from_dict(item)
            # Draft against the résumé this posting best matches (falls back to the first).
            history = by_label.get(item.get("best_resume", "") or "", default_history)
            letters[key] = cover_letter_fn(jp, cfg, history)
            generated += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [publish] cover-letter generation failed for "
                  f"{item.get('title', '?')} @ {item.get('employer', '?')}: "
                  f"{type(e).__name__}: {e}")
    return letters, generated


def load_metrics(user_dir: Path, week: str, jobs: list[dict]) -> dict:
    cache_path = user_dir / "postings_cache" / f"{week}.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    surfaced = cache.get("ranked", cache.get("screened", len(jobs)))

    applied_logged = 0
    log_path = user_dir / "log" / "job_search_log.csv"
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("week_ending") == week and row.get("activity_type") == "applied_to_job":
                    applied_logged += 1

    return {
        "surfaced": surfaced,
        # "approved" isn't known until the dashboard/RADMACHINE round-trip completes — the
        # Worker's own /api/approve + /api/approvals track that; publish just seeds it at 0.
        "approved": 0,
        "applied_logged": applied_logged,
        # TODO: wa-unemployment-copilot doesn't track employer responses yet. Wire this up once
        # that's added (e.g. a new ESD-adjacent activity_type or a separate responses log).
        "responses": 0,
    }


def load_applicant_and_resumes(user: str, data_root: str | None = None) -> tuple[dict, dict]:
    """Best-effort (applicant dict, {résumé label -> PDF filename}) from the user's config.

    These ride along in the publish payload so the Worker can build the submissions CSV +
    CoWork prompt from KV alone (the dashboard's self-serve "get my stuff" buttons). A missing
    or unloadable config just yields ({}, {}) — publish proceeds and the Worker treats them as
    absent (blank résumé column, prompt with whatever applicant fields it has). Uses the same
    lazy copilot import as the cover-letter/notify paths so the stdlib publish path keeps no hard
    dependency on the package.
    """
    try:
        repo_src = Path(__file__).resolve().parent.parent / "src"
        if str(repo_src) not in sys.path:
            sys.path.insert(0, str(repo_src))
        from copilot.config import load as load_config
    except Exception:  # noqa: BLE001
        return {}, {}
    try:
        cfg = load_config(user, data_root=data_root)
    except Exception:  # noqa: BLE001
        return {}, {}
    applicant = cfg.get("applicant", {}) or {}
    if not isinstance(applicant, dict):
        applicant = {}
    resumes = {}
    try:
        for r in cfg.resumes():
            label = r.get("label", "")
            filename = Path(r.get("path", "")).name
            if label and filename:
                resumes[label] = filename
    except Exception:  # noqa: BLE001
        resumes = {}
    return applicant, resumes


def build_payload(user: str, week: str, data_root: str | None = None) -> tuple[dict, int]:
    """Returns (payload, letters_generated) — the count is for reporting only."""
    user_dir = user_root(user, data_root)
    queue = load_queue(user_dir, week)
    postings = load_ranked_postings(user_dir, week)
    if not postings:
        # Fall back to the (smaller) apply queue for older weeks / partial pipeline runs where
        # discover's postings_cache isn't available.
        postings = list(queue.get("items", []) or [])
    if not postings:
        raise FileNotFoundError(
            f"no postings found for week {week}: neither postings_cache/{week}.json nor "
            f"applications/{week}/queue.json exist under {user_dir}"
        )

    letters, generated = generate_top_letters(user, postings, queue_letters(queue), data_root)
    jobs = [_job_from_posting(p, letters.get(p.get("dedup_key", ""), "")) for p in postings]
    metrics = load_metrics(user_dir, week, jobs)
    applicant, resumes = load_applicant_and_resumes(user, data_root)
    return {"week": week, "jobs": jobs, "metrics": metrics,
            "user": user, "applicant": applicant, "resumes": resumes}, generated


def _clean_cf_value(v):
    """Tolerate a value pasted WITH its header-name prefix, e.g.
    'CF-Access-Client-Id: <id>' or 'CF-Access-Client-Secret:  <secret>' — strip the label +
    surrounding whitespace so the raw token is used."""
    v = (v or "").strip()
    low = v.lower()
    for pref in ("cf-access-client-id:", "cf-access-client-secret:"):
        if low.startswith(pref):
            return v[len(pref):].strip()
    return v


def cf_access_headers() -> dict[str, str]:
    """CF-Access-Client-Id/Secret headers for a Cloudflare Access service token, if configured.

    Optional and additive to the PUBLISH_TOKEN bearer auth — empty dict if the env vars aren't
    set, so requests behave exactly as before Access was added in front of the Worker route.
    """
    client_id = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_ID"))
    client_secret = _clean_cf_value(os.environ.get("WA_COPILOT_CF_ACCESS_CLIENT_SECRET"))
    if client_id and client_secret:
        return {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": client_secret}
    return {}


def _import_notify_deps():
    """Best-effort import of this repo's copilot package, for the review-ready email only.

    Same lazy-import pattern as `_import_cover_letter_generator` — a missing/broken copilot
    install just skips the email instead of blocking the rest of publish.
    """
    repo_src = Path(__file__).resolve().parent.parent / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    from copilot.config import load as load_config
    from copilot.mailer import send, MailError
    from copilot.email_render import render_weekly_review, weekly_review_plaintext
    return load_config, send, MailError, render_weekly_review, weekly_review_plaintext


def _posted_label(iso: str, today: date, max_days: int = 45) -> str | None:
    """'posted 2d ago' style label from an ISO posted_date, or None if absent/stale/unparseable."""
    if not iso:
        return None
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    days = (today - d).days
    if days < 0 or days > max_days:
        return None
    if days == 0:
        return "posted today"
    return f"posted {days}d ago"


def _work_mode(job: dict) -> str | None:
    """The only work mode we can infer reliably from the posting data is 'Remote'.

    Suppressed when the location already reads 'Remote', so the meta line doesn't say
    'Remote · Remote'.
    """
    if "remote" in (job.get("location", "") or "").lower():
        return None
    return "Remote" if job.get("remote") else None


def _truncate(text: str, limit: int = 170) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def _short_reason(reason: str) -> str:
    """A concise chip label from a ranking reason: keep the headline, drop the trailing list.

    e.g. 'fits this week's focus: seattle, remote' -> 'fits this week's focus';
         'title overlaps your past roles (ops, program)' -> 'title overlaps your past roles'.
    """
    for delim in (":", "(", "—", "–"):
        reason = reason.split(delim, 1)[0]
    return " ".join(reason.split())[:56].rstrip(" .,;")


def weekly_review_view_models(jobs: list[dict], top_n: int = 3, today: date | None = None) -> list[dict]:
    """Turn the published job dicts into email-render view models (best-first, top N).

    match_pct and the "Surfaced for: ..." label are read straight from the job dict — both were
    computed ONCE in discover.py (explain()) and already carried through _job_from_posting(), so
    this is the identical 0-100 value/label the dashboard shows for the same job, never a
    separately-derived one. Falls back to a reasons-derived short label only for legacy cache
    entries published before top_factor existed.
    """
    today = today or date.today()
    ranked = sorted(jobs, key=lambda j: j.get("match_score", 0) or 0, reverse=True)
    top = ranked[:max(0, top_n)]
    out = []
    for j in top:
        top_factor = j.get("top_factor") or ""
        if top_factor:
            match_reason = f"Surfaced for: {top_factor}"
        else:
            reasons = j.get("reasons") or [r.strip() for r in (j.get("why_matched", "") or "").split(";") if r.strip()]
            match_reason = _short_reason(reasons[0]) if reasons else None
        out.append({
            "title": j.get("title", ""),
            "org": j.get("org", ""),
            "location": j.get("location", ""),
            "mode": _work_mode(j),
            "match_pct": j.get("match_pct"),
            "match_reason": match_reason,
            "resume": j.get("resume", ""),
            "posted_label": _posted_label(j.get("posted_date", ""), today),
            "blurb": _truncate(j.get("description") or j.get("why_matched") or ""),
            "url": j.get("url", ""),
        })
    return out


def resolve_dev_recipient(cfg, dev_to: str | None) -> str:
    """Where the --dev-test review email goes: an explicit --dev-to, else config `email.dev_to`,
    else env WA_COPILOT_EMAIL_DEV_TO. Deliberately does NOT fall back to the prod `email.to` —
    the whole point of a dev send is to reach the operator, not the real user."""
    return (dev_to
            or str(cfg.get("email.dev_to") or "").strip()
            or os.environ.get("WA_COPILOT_EMAIL_DEV_TO", "").strip())


def send_review_ready_email(user: str, week: str, worker_url: str, payload: dict,
                            data_root: str | None = None, dev: bool = False,
                            dev_to: str | None = None) -> None:
    """Best-effort 'your weekly matches are ready to review' email. Never raises.

    Renders the periwinkle weekly-review template (`copilot.email_render`) from this week's real
    ranked jobs and sends it over the same Gmail-SMTP transport `run_weekly.py --steps notify`
    uses. Skips quietly (with a printed reason) if the copilot package isn't importable, the
    user's config can't load, or `email.enabled` is false in their config.yaml.

    Dev vs prod: with `dev=False` (default) it's the real weekly send to the prod user (config
    `email.to` — Alex). With `dev=True` it's a dev/test send — redirected to the operator (see
    resolve_dev_recipient), subject prefixed [DEV], and the dashboard link carries `?dev=1` so a
    Submit from that email is a no-op the Worker won't record in KV. Same rendered content either
    way, so the dev send is a faithful preview of the prod experience.
    """
    try:
        load_config, send, MailError, render_weekly_review, weekly_review_plaintext = _import_notify_deps()
    except Exception as e:  # noqa: BLE001
        print(f"  [publish] review-ready email skipped (copilot import unavailable: {type(e).__name__}: {e})")
        return

    try:
        cfg = load_config(user, data_root=data_root)
    except Exception as e:  # noqa: BLE001
        print(f"  [publish] review-ready email skipped (config load failed: {type(e).__name__}: {e})")
        return

    if not cfg.email_enabled:
        print("  [publish] review-ready email skipped (email.enabled is false in config.yaml)")
        return

    recipient = None
    if dev:
        recipient = resolve_dev_recipient(cfg, dev_to)
        if not recipient:
            print("  [publish] DEV review email skipped: no dev recipient "
                  "(--dev-to, config email.dev_to, or env WA_COPILOT_EMAIL_DEV_TO)")
            return

    jobs = payload.get("jobs", []) or []
    surfaced = (payload.get("metrics", {}) or {}).get("surfaced") or len(jobs)
    review_url = worker_url.rstrip("/") + "/"
    if dev:
        review_url += "?dev=1"   # dashboard shows a DEV banner and marks Submit as non-recording
    view_models = weekly_review_view_models(jobs)

    prefix = "[DEV] " if dev else ""
    subject = f"{prefix}Your weekly job matches are ready to review — week ending {week}"
    # The "Report a bug" button opens a reply to whoever this email is From (the operator's alias),
    # so replies land in the operator's inbox just like hitting Reply would.
    try:
        reply_to = cfg.email_from or cfg.get_secret("smtp_user") or ""
    except Exception:  # noqa: BLE001
        reply_to = cfg.email_from or ""
    html = render_weekly_review(week=week, surfaced=surfaced, jobs=view_models,
                                review_url=review_url, reply_to=reply_to)
    text = weekly_review_plaintext(week=week, surfaced=surfaced, jobs=view_models, review_url=review_url)
    try:
        send(cfg, subject, text, html_body=html, to=recipient)
        print(f"  [publish] {'DEV ' if dev else ''}review-ready email sent to {recipient or cfg.email_to}")
    except MailError as e:
        print(f"  [publish] review-ready email FAILED: {e}")


def publish(worker_url: str, payload: dict, token: str) -> None:
    url = worker_url.rstrip("/") + "/api/week"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "wa-copilot-publisher/1.0",
    }
    headers.update(cf_access_headers())
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        print(f"  [publish] {resp.status} {body}")


def _normalize_worker_url(url):
    """Accept a Worker URL saved without a scheme (e.g. 'x.workers.dev') by assuming https://."""
    url = (url or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="copilot user id (matches <Desktop>/wa-unemployment-copilot/<user>)")
    ap.add_argument("--week", default=None, help="week-ending date YYYY-MM-DD (default: current claim week)")
    ap.add_argument("--worker-url", default=os.environ.get("WA_COPILOT_WORKER_URL"),
                    help="dashboard Worker base URL (default: env WA_COPILOT_WORKER_URL)")
    ap.add_argument("--data-root", default=None, help="override data root (default: <Desktop>/wa-unemployment-copilot)")
    ap.add_argument("--dry-run", action="store_true", help="print the payload; don't call the Worker")
    ap.add_argument("--dev-test", action="store_true",
                    help="dev/test run: still PUTs the week to the Worker, but sends the review "
                         "email to the dev recipient (--dev-to / email.dev_to / WA_COPILOT_EMAIL_DEV_TO) "
                         "with a ?dev=1 dashboard link so a Submit from it is NOT recorded in KV")
    ap.add_argument("--dev-to", default=None,
                    help="dev-test review-email recipient (overrides config email.dev_to / "
                         "env WA_COPILOT_EMAIL_DEV_TO)")
    args = ap.parse_args()
    args.worker_url = _normalize_worker_url(args.worker_url)

    week = args.week or week_ending().isoformat()

    try:
        payload, generated = build_payload(args.user, week, args.data_root)
    except FileNotFoundError as e:
        print(f"[publish_week] {e}", file=sys.stderr)
        return 1

    top5 = sorted(payload["jobs"], key=lambda j: j.get("match_score", 0), reverse=True)[:TOP_N_LETTERS]
    with_letter = sum(1 for j in top5 if j.get("cover_letter"))
    print(f"  [publish] {with_letter}/{len(top5)} top-ranked job(s) have a cover letter "
          f"({generated} generated this run, {with_letter - generated} reused from the apply queue).")

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    if not args.worker_url:
        print("[publish_week] --worker-url or env WA_COPILOT_WORKER_URL is required (unless --dry-run)", file=sys.stderr)
        return 3

    token = os.environ.get("WA_COPILOT_PUBLISH_TOKEN")
    if not token:
        print("[publish_week] env WA_COPILOT_PUBLISH_TOKEN is required (unless --dry-run)", file=sys.stderr)
        return 3

    # Decoupled: a Worker/publish failure must NOT suppress the review email. Attempt the publish,
    # but ALWAYS send the review email afterward (its dashboard link may point at a stale week if
    # the publish failed). The failure is still surfaced via a non-zero exit so CI/Actions flags it
    # — it just no longer costs the user their weekly email (root cause of the 2026-09 outage).
    publish_ok = True
    try:
        publish(args.worker_url, payload, token)
    except urllib.error.URLError as e:
        publish_ok = False
        print(f"[publish_week] WARNING: could not reach the Worker; the dashboard was NOT updated "
              f"this week and its review link may be stale: {e}", file=sys.stderr)
        print("[publish_week] sending the review email anyway (decoupled from publish).", file=sys.stderr)

    send_review_ready_email(args.user, week, args.worker_url, payload, args.data_root,
                            dev=args.dev_test, dev_to=args.dev_to)

    return 0 if publish_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
