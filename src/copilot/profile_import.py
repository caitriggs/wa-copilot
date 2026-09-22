"""
Profile import — build history.json from a LinkedIn data export or a résumé.
============================================================================

Discovery ranking and draft generation are much better when they know your real experience.
`import_history(cfg)` produces profile/history.json:

    { "headline": str, "summary": str, "skills": [str, ...],
      "roles": [ {"title","company","start","end","bullets":[...]} ],
      "raw_text": str, "source": "export|resume|authed" }

Dispatch on config `profile.linkedin_import`:
  - "export": parse a LinkedIn "Download your data" zip (Profile/Positions/Skills CSVs). Reliable.
  - "resume": parse profile/resume.(pdf|docx|txt|md). PDF needs the optional pdfplumber package;
#    .docx is read with the stdlib.
  - "authed": authenticated self-profile fetch (Phase 3, not implemented) — falls back to resume.

Nothing here is submitted anywhere; it only reads your own files into a local JSON.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

from . import paths
from .config import Config

# Cheap/fast model for role extraction only — never used for cover-letter content, which stays on
# whatever `draft.llm.model` the user configured (or the built-in template).
_ROLES_MODEL = "claude-haiku-4-5"

# Liberal on purpose — résumés use all kinds of section titles for the same thing.
_SKILL_HEADING_WORDS = (
    r"core\s+skills|core\s+competencies|key\s+skills|technical\s+skills|"
    r"skills\s*(?:&|and)\s*expertise|areas?\s+of\s+expertise|skills"
)
_SKILL_HEADING = re.compile(rf"^\s*(?:{_SKILL_HEADING_WORDS})\s*:?\s*$", re.I)
_INLINE_SKILLS = re.compile(rf"^\s*(?:{_SKILL_HEADING_WORDS})\s*:\s*(.+)$", re.I)
_SPLIT = re.compile(r"[,;•|]| - |•")
# A leading category label on a skills line, e.g. "Tools: Jira, Confluence" -> strip "Tools: " so
# only the actual skill terms get split out (common when a résumé groups skills by category).
_SKILL_CATEGORY_PREFIX = re.compile(r"^[A-Z][A-Za-z0-9&/\- ]{1,50}:\s*")

# Generic/common-English or boilerplate words that show up as skill-list fragments — split off a
# department list ("Engineering, Design, Art, Audio, Animation") or a leadership blurb, not
# actually a skill in themselves. Bare single words ONLY (see _is_generic_skill): a compound
# phrase like "sprint planning" or "team leadership" is a real skill and passes through untouched.
# Deliberately does NOT include tool/tech/domain names (Jira, Python, SQL, QA, ...) — those are
# real skills even standalone.
# Deliberately narrow: soft-skill/business words like "leadership", "management", "strategy",
# "quality", "communication" are commonly self-declared as real skills on résumés ("Skills:
# Leadership, Communication, ...") and must NOT be blocked. This list is limited to words that
# only ever showed up as department/discipline names or process-noun fragments split out of a
# collaboration blurb (e.g. "Cross-Discipline Leadership: Engineering, Design, Art, Audio,
# Animation, QA...") or a run-on phrase ("...contract and SOW / review, ...") — never a skill a
# person would plausibly self-declare standalone.
_GENERIC_SKILL_WORDS = {
    "review", "art", "audio", "design", "animation", "engineering", "risk", "planning",
}
# A fragment starting with a bare conjunction/preposition is a broken mid-phrase split (comma or
# line-wrap artifact), not a real skill — e.g. "and Services partnership" from a run-on sentence.
_LEADING_STOPWORDS = {"and", "or", "the", "a", "an", "with", "for", "of", "to", "in", "on"}


def _is_generic_skill(s: str) -> bool:
    """True if `s` is boilerplate/generic filler rather than a real skill: a broken mid-phrase
    fragment, or a bare single common-English word. Multi-word phrases and tool/tech/domain names
    (even one word, like "Jira" or "QA") are never flagged."""
    low = (s or "").strip().lower()
    if not low:
        return True
    words = low.split()
    if words[0] in _LEADING_STOPWORDS:
        return True
    return len(words) == 1 and low in _GENERIC_SKILL_WORDS


def filter_generic_skills(skills: list[str]) -> list[str]:
    """Drop boilerplate fragments from a skills list, keeping real (including single-word,
    non-generic) skills. Applied both when a résumé's skills are first parsed and again at match
    time (discover.py, draft.py), so stale history.json files built before this filter existed
    still get the benefit without needing a re-import."""
    return [s for s in skills if not _is_generic_skill(s)]


def _empty_history(source: str) -> dict:
    return {"headline": "", "summary": "", "skills": [], "roles": [], "highlights": [],
            "raw_text": "", "source": source}


# Words that signal a job-title/headline line in a résumé.
ROLE_KEYWORDS = (
    "scientist", "engineer", "manager", "analyst", "developer", "lead", "director",
    "architect", "designer", "consultant", "specialist", "researcher", "data", "product",
    "program", "operations", "marketing", "administrator", "coordinator",
)
_SEP_RE = re.compile(r"\s[—–\-|·]\s")
_BULLET_RE = re.compile(r"^[-•*·]\s+")


def _headline_from_lines(non_empty: list[str]) -> str:
    """Find a short title-ish line near the top, stripping any leading name and trailing punctuation."""
    for ln in non_empty[:8]:
        if not any(k in ln.lower() for k in ROLE_KEYWORDS):
            continue
        for seg in _SEP_RE.split(ln):            # "Jordan Lee — Data Scientist" -> "Data Scientist"
            if any(k in seg.lower() for k in ROLE_KEYWORDS) and len(seg.split()) <= 8:
                return seg.strip().rstrip(",.;:")
        if len(ln.split()) <= 10:                # only use a whole line if it reads like a title, not a summary
            return ln.strip().rstrip(",.;:")
    return ""


def _highlights_from_text(text: str) -> list[str]:
    """Bullet lines from the résumé, usable as candidate achievements in drafts."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if _BULLET_RE.match(s):
            out.append(_BULLET_RE.sub("", s).strip())
    return [h for h in out if len(h) > 8][:8]


# --------------------------------------------------------------------------- LinkedIn export

def _read_csv_from_zip(zf: zipfile.ZipFile, *names) -> list[dict]:
    """Read the first matching CSV (case-insensitive) from the zip as dicts."""
    lookup = {n.lower(): n for n in zf.namelist()}
    for want in names:
        # exact or basename match
        for key, real in lookup.items():
            if key == want.lower() or key.endswith("/" + want.lower()) or Path(key).name.lower() == want.lower():
                with zf.open(real) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace").read()
                # LinkedIn sometimes prefixes a notes line before the header; skip to header row.
                lines = text.splitlines()
                start = 0
                for i, ln in enumerate(lines[:5]):
                    if "," in ln:
                        start = i
                        break
                return list(csv.DictReader(lines[start:]))
    return []


def _from_export(zip_path: Path) -> dict:
    hist = _empty_history("export")
    if not zip_path.exists():
        print(f"  [profile_import] export not found at {zip_path}")
        return hist
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        print(f"  [profile_import] {zip_path} is not a valid zip.")
        return hist

    with zf:
        profile = _read_csv_from_zip(zf, "Profile.csv")
        if profile:
            p = profile[0]
            hist["headline"] = (p.get("Headline") or p.get("headline") or "").strip()
            hist["summary"] = (p.get("Summary") or p.get("summary") or "").strip()
        for row in _read_csv_from_zip(zf, "Positions.csv"):
            title = (row.get("Title") or "").strip()
            company = (row.get("Company Name") or row.get("Company") or "").strip()
            if not (title or company):
                continue
            desc = (row.get("Description") or "").strip()
            bullets = [b.strip("-• \t") for b in desc.splitlines() if b.strip()][:6]
            hist["roles"].append({
                "title": title, "company": company,
                "start": (row.get("Started On") or "").strip(),
                "end": (row.get("Finished On") or "").strip(),
                "bullets": bullets,
            })
        skills = []
        for row in _read_csv_from_zip(zf, "Skills.csv"):
            name = (row.get("Name") or row.get("Skill") or "").strip()
            if name:
                skills.append(name)
        hist["skills"] = _dedupe(skills)

    if not hist["headline"] and hist["roles"]:
        hist["headline"] = hist["roles"][0]["title"]
    print(f"  [profile_import] export -> {len(hist['roles'])} roles, {len(hist['skills'])} skills")
    return hist


# --------------------------------------------------------------------------- résumé

def _resume_text(path: Path) -> str:
    if not path.exists():
        return ""
    if path.suffix.lower() == ".pdf":
        try:
            import pdfplumber  # type: ignore
        except ImportError:
            print("  [profile_import] pdfplumber not installed; can't read a PDF résumé. "
                  "Install it or use a .txt/.md résumé or a LinkedIn export.")
            return ""
        try:
            with pdfplumber.open(path) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        except Exception as e:  # noqa: BLE001
            print(f"  [profile_import] failed to read PDF: {type(e).__name__}: {e}")
            return ""
    if path.suffix.lower() == ".docx":
        # Word .docx = a zip of XML; pull the paragraph text with the stdlib (no extra dependency).
        try:
            import zipfile as _zip, re as _re, html as _html
            with _zip.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = _re.sub(r"</w:p>", "\n", xml)      # paragraph breaks -> newlines
            xml = _re.sub(r"<[^>]+>", "", xml)         # drop the tags
            return _html.unescape(xml)
        except Exception as e:  # noqa: BLE001
            print(f"  [profile_import] failed to read .docx: {type(e).__name__}: {e}")
            return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _is_section_heading(line: str) -> bool:
    """A short, ALL-CAPS line (ignoring punctuation/digits) — the start of a new résumé section,
    e.g. "PROFESSIONAL EXPERIENCE" or "EDUCATION". Résumés often run straight from the last skills
    line into the next heading with no blank line, so this is what actually stops the skills scan."""
    s = line.strip()
    if not s or len(s.split()) > 5:
        return False
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _skills_from_text(text: str) -> list[str]:
    skills: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _INLINE_SKILLS.match(line)
        if m:
            skills += _SPLIT.split(m.group(1))
            continue
        if _SKILL_HEADING.match(line):
            # Generous cap (skills sections run long when grouped by category) bounded by the next
            # real section heading, not a fixed short window.
            for nxt in lines[i + 1:i + 30]:
                if not nxt.strip() or _is_section_heading(nxt):
                    break
                body = _SKILL_CATEGORY_PREFIX.sub("", nxt, count=1)
                skills += _SPLIT.split(body)
    cleaned = [s.strip(" .\t-•") for s in skills]
    return filter_generic_skills(_dedupe([s for s in cleaned if 1 < len(s) <= 40]))


def _from_resume(path: Path) -> dict:
    hist = _empty_history("resume")
    text = _resume_text(path)
    if not text.strip():
        print(f"  [profile_import] no résumé text read from {path}")
        return hist
    hist["raw_text"] = text
    non_empty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hist["headline"] = _headline_from_lines(non_empty)   # a title, not your name
    if non_empty:
        hist["summary"] = " ".join(non_empty[1:4])[:600]
    hist["skills"] = _skills_from_text(text)
    hist["highlights"] = _highlights_from_text(text)
    print(f"  [profile_import] resume -> headline {'set' if hist['headline'] else 'not found'}, "
          f"{len(hist['skills'])} skills, {len(hist['highlights'])} highlights")
    return hist


def _llm_roles(cfg: Config, resume_text: str) -> list[dict] | None:
    """Extract past job titles from résumé text with a cheap LLM call — a RANKING signal only
    (feeds discover.py's title_similarity). Returns None on any problem so the caller falls back
    to no roles (the regex parser doesn't extract titles/dates/companies reliably across résumé
    formats, and title_similarity simply contributes nothing when roles is empty).

    Deliberately NOT used for cover-letter content: llm.py's _profile_text() always prefers
    history["raw_text"] when present (résumé mode always sets it), so a bad extraction here can't
    leak wrong job history into an outbound cover letter — only ranking is affected.
    """
    from . import llm
    if not llm.has_api(cfg) or not resume_text.strip():
        return None
    try:
        import anthropic
        client = llm._make_client(anthropic, cfg.get_secret("anthropic"))
        system = (
            "Extract this person's past job titles from their résumé text, most recent first. "
            "Return ONLY minified JSON: a list of objects with keys \"title\" and \"company\". "
            "Use ONLY titles and companies that literally appear in the text — do not invent, "
            "infer, or normalize/rewrite them. If nothing is identifiable, return []. JSON only, "
            "no prose, no markdown fences."
        )
        resp = client.messages.create(
            model=_ROLES_MODEL, max_tokens=600, system=system,
            messages=[{"role": "user", "content": resume_text[:6000]}], timeout=30,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
        roles = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if title:
                roles.append({"title": title, "company": str(item.get("company", "")).strip(),
                              "start": "", "end": "", "bullets": []})
        return roles or None
    except Exception as e:  # noqa: BLE001 — never let role extraction block import
        print(f"  [profile_import] LLM role extraction failed ({type(e).__name__}: {str(e)[:200]}); "
              "ranking will treat this résumé as having no title history.")
        return None


# --------------------------------------------------------------------------- driver

def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        k = it.strip().lower()
        if it.strip() and k not in seen:
            seen.add(k)
            out.append(it.strip())
    return out


def _history_path(root: Path, slug: str) -> Path:
    """Per-resume history file. The default resume keeps the legacy `history.json` name so
    single-history readers (and older data folders) are unaffected."""
    if slug == "resume":
        return root / "profile" / "history.json"
    return root / "profile" / f"history.{slug}.json"


def import_histories(cfg: Config, data_root=None, force: bool = False) -> list[dict]:
    """Build/refresh a history per configured resume. Returns [{label, slug, history}].

    `profile.resumes` -> one `history.<slug>.json` each (plus `history.json` mirroring the first,
    for single-history readers). Export mode stays single (built from the LinkedIn zip).
    """
    data_root = data_root if data_root is not None else cfg.data_root
    root = paths.user_root(cfg.user, data_root)
    (root / "profile").mkdir(parents=True, exist_ok=True)
    mode = cfg.get("profile.linkedin_import", "resume")

    if mode == "export":
        hist = _from_export(root / cfg.get("profile.data_export_path", "profile/linkedin-export.zip"))
        hist["resume_label"], hist["resume_slug"] = "Résumé", "resume"
        _history_path(root, "resume").write_text(json.dumps(hist, indent=2), encoding="utf-8")
        return [{"label": "Résumé", "slug": "resume", "history": hist}]

    if mode == "authed":
        print("  [profile_import] authed self-fetch is Phase 3 (not implemented); using résumé(s).")

    results = []
    for r in cfg.resumes():
        hist = _from_resume(root / r["path"])
        hist["resume_label"], hist["resume_slug"] = r["label"], r["slug"]
        roles = _llm_roles(cfg, hist.get("raw_text", ""))
        if roles:
            hist["roles"] = roles
            print(f"  [profile_import] {r['label']}: extracted {len(roles)} title(s) for ranking.")
        _history_path(root, r["slug"]).write_text(json.dumps(hist, indent=2), encoding="utf-8")
        results.append({"label": r["label"], "slug": r["slug"], "history": hist})
    if results:  # mirror the first resume to the legacy path
        (root / "profile" / "history.json").write_text(
            json.dumps(results[0]["history"], indent=2), encoding="utf-8")
    return results


def ensure_histories(cfg: Config, data_root=None) -> list[dict]:
    """Load every configured resume's history, importing all if any is missing.

    Returns [{label, slug, history}] — one entry per `profile.resumes` (or a single default).
    """
    data_root = data_root if data_root is not None else cfg.data_root
    root = paths.user_root(cfg.user, data_root)
    resumes = cfg.resumes()
    if any(not _history_path(root, r["slug"]).exists() for r in resumes):
        return import_histories(cfg, data_root)
    out = []
    for r in resumes:
        try:
            hist = json.loads(_history_path(root, r["slug"]).read_text(encoding="utf-8"))
        except Exception:
            hist = {}
        out.append({"label": r["label"], "slug": r["slug"], "history": hist})
    return out


def import_history(cfg: Config, data_root=None, force: bool = False) -> Path:
    """Back-compat single-history entrypoint: builds all resume histories, returns history.json."""
    import_histories(cfg, data_root, force=force)
    return paths.user_root(cfg.user, data_root if data_root is not None else cfg.data_root) / "profile" / "history.json"


def ensure_history(cfg: Config, data_root=None) -> dict:
    """Back-compat single-history accessor: the first (default) resume's history dict."""
    hs = ensure_histories(cfg, data_root)
    return hs[0]["history"] if hs else {}
