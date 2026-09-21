"""
Per-user config loading + secret resolution.
============================================

Loads a user's `config.yaml` (from their Desktop data folder), applies defaults, validates the
important fields, and resolves secrets on demand from the OS keyring (preferred) or a gitignored
`secrets.env` fallback. The Config object never stores secret VALUES and never prints them.

Secrets are referenced in config by a `secret_ref` (e.g. "linkedin", "usajobs", "smtp_pass").
Keyring service name is `wa-unemployment-copilot:<user>`; secrets.env uses UPPERCASE keys.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from . import paths

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    raise SystemExit("Missing dependency PyYAML. Run: python -m pip install -r requirements.txt")

try:
    import keyring  # optional; falls back to secrets.env
except ImportError:  # pragma: no cover
    keyring = None

KEYRING_SERVICE = "wa-unemployment-copilot"

# Environment-variable fallback for secrets, consulted AFTER the keyring and secrets.env so a
# Desktop deployment is unaffected, but a cloud/CI session (no keyring, no secrets.env) can supply
# creds through the environment instead. Explicit aliases first so operator-chosen names work
# (e.g. a Gmail app password stored as WA_COPILOT_GMAIL_APP_PASSWORD), then a generic
# WA_COPILOT_<REF> name.
_SECRET_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "smtp_pass": ("WA_COPILOT_SMTP_PASS", "WA_COPILOT_GMAIL_APP_PASSWORD"),
    "smtp_user": ("WA_COPILOT_SMTP_USER",),
}


def _resume_slug(label: str) -> str:
    """Filename-safe slug for a resume label, e.g. 'QA Test Lead' -> 'qa-test-lead'."""
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:48]
    return s or "resume"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _secret_from_env(ref: str) -> Optional[str]:
    names = list(_SECRET_ENV_ALIASES.get(ref, ())) + ["WA_COPILOT_" + ref.strip().upper()]
    for name in names:
        val = os.environ.get(name)
        if val:
            return val.strip()
    return None


class ConfigError(ValueError):
    """Raised when a user's config is missing required fields or is malformed."""


DEFAULTS: dict[str, Any] = {
    "email": {"enabled": False, "to": ""},
    "profile": {"linkedin_url": "", "linkedin_import": "resume",
                "resume_path": "profile/resume.pdf",
                "data_export_path": "profile/linkedin-export.zip"},
    "search": {
        "titles": [], "locations": [], "keywords_include": [], "keywords_exclude": [],
        "comp_min": None,
        "weekly_notes": "",
        # weekly_notes acts as an OVERRIDE for the week's search/ranking (see focus.py).
        "focus": {"enabled": True},
    },
    "sources": {
        "usajobs": {"enabled": True},
        "adzuna": {"enabled": False, "country": "us", "results_per_page": 25,
                   "salary_filter": False},
        "jooble": {"enabled": False},
        "careerjet": {"enabled": False, "locale_code": "en_US", "results_per_page": 25},
        "remotive": {"enabled": False, "results_per_page": 25},
        "weworkremotely": {"enabled": False, "categories": ["remote-programming-jobs"]},
        "worksourcewa": {"enabled": True, "saved_search_rss": ""},
        "feeds": {"enabled": True, "rss_urls": []},
        "email_alerts": {"enabled": False, "imap_host": "imap.gmail.com", "mailbox": "INBOX",
                         "since_days": 7, "providers": ["indeed", "linkedin", "worksourcewa"]},
        "authed": [],
    },
    "targets_per_week": 3,
    "draft": {"llm": {"enabled": False, "model": "claude-sonnet-5", "max_words": 300}},
    "apply": {"mode": "stage", "top_n": 3, "executor": "cowork"},
    "claim_assist": {"enabled": False, "url": "https://secure.esd.wa.gov/",
                     "autofill_attempt": False, "headless": False},
    "ranking": {
        "comp_fit": 2.0, "keyword_hits": 1.5, "title_similarity": 2.0,
        "skill_overlap": 1.5, "weekly_notes_boost": 1.0, "focus_match": 3.5, "top_n_drafts": 5,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base (dicts merge; other types replace)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Validated per-user configuration with lazy secret resolution."""

    def __init__(self, user: str, data: dict, data_root: str | os.PathLike | None = None):
        self.user = paths.safe_user(user)
        self.data = data
        self.data_root = data_root

    # --- dotted access, e.g. cfg.get("search.comp_min") ---
    def get(self, dotted: str, default=None):
        cur: Any = self.data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    # Frequently used shortcuts.
    @property
    def targets_per_week(self) -> int:
        return int(self.get("targets_per_week", 3))

    @property
    def email_enabled(self) -> bool:
        # config value wins when set; otherwise a cloud/CI env flag can enable email without a
        # config.yaml on disk (WA_COPILOT_EMAIL_ENABLED=1).
        val = self.get("email.enabled")
        if val is not None:
            return bool(val)
        return _env_flag("WA_COPILOT_EMAIL_ENABLED")

    @property
    def email_to(self) -> str:
        return str(self.get("email.to") or os.environ.get("WA_COPILOT_EMAIL_TO", "") or "")

    @property
    def weekly_notes(self) -> str:
        """Effective weekly-notes override that steers this week's search/ranking (see focus.py).

        A `weekly_notes.txt` steering file in the user's data folder — written by the review
        dashboard round-trip (scripts/fetch_notes.py) — takes precedence over config.yaml
        `search.weekly_notes`, so it can be updated without editing the config. If that file
        exists it is authoritative (even when empty, meaning "no focus this week"); otherwise the
        config value is used. Returned stripped.
        """
        try:
            p = paths.weekly_notes_path(self.user, self.data_root)
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        return str(self.get("search.weekly_notes", "") or "").strip()

    @property
    def email_from(self) -> str:
        """The From address: config `email.from`, else env WA_COPILOT_EMAIL_FROM, else empty
        (the mailer then falls back to the SMTP login user)."""
        return str(self.get("email.from") or os.environ.get("WA_COPILOT_EMAIL_FROM", "") or "")

    def resumes(self) -> list[dict]:
        """The user's resumes as [{label, slug, path, titles}], best-match-badged in the UI.

        Multi-resume support: `profile.resumes` is a list of {label, path, titles?}. Each resume
        is imported to its own history and every posting is scored against all of them (one ranked
        pool — see discover.explain_best), badged with the resume it matches best. Falls back to a
        single synthesized resume from `profile.resume_path` when `profile.resumes` is absent, so
        existing single-resume configs keep working unchanged.
        """
        raw = self.get("profile.resumes") or []
        out = []
        if isinstance(raw, list) and raw:
            for i, r in enumerate(raw):
                if not isinstance(r, dict):
                    continue
                label = str(r.get("label") or f"Resume {i + 1}").strip()
                path = str(r.get("path") or "").strip()
                if not path:
                    continue
                titles = [str(t).strip() for t in (r.get("titles") or []) if str(t).strip()]
                out.append({"label": label, "slug": _resume_slug(label), "path": path,
                            "titles": titles})
        if not out:
            path = str(self.get("profile.resume_path", "profile/resume.pdf") or "profile/resume.pdf")
            out.append({"label": "Résumé", "slug": "resume", "path": path, "titles": []})
        return out

    def enabled_sources(self) -> list[str]:
        srcs = []
        for name in ("usajobs", "adzuna", "jooble", "careerjet", "remotive", "weworkremotely",
                     "worksourcewa", "feeds", "email_alerts"):
            if self.get(f"sources.{name}.enabled", False):
                srcs.append(name)
        for entry in (self.get("sources.authed", []) or []):
            if entry.get("enabled"):
                srcs.append(f"authed:{entry.get('site')}")
        return srcs

    # --- secrets ---
    def get_secret(self, ref: str) -> Optional[str]:
        """Resolve a secret by ref: OS keyring, then secrets.env, then a WA_COPILOT_* env var.
        Never logged."""
        return get_secret(self.user, ref, data_root=self.data_root)

    def __repr__(self) -> str:  # never leak secrets (we don't hold any, but be explicit)
        return f"<Config user={self.user!r} sources={self.enabled_sources()}>"


def get_secret(user: str, ref: str, data_root=None) -> Optional[str]:
    """Keyring first (service 'wa-unemployment-copilot:<user>'), then gitignored secrets.env, then
    a WA_COPILOT_* environment variable (for keyring-less cloud/CI sessions). Never logged."""
    if not ref:
        return None
    service = f"{KEYRING_SERVICE}:{paths.safe_user(user)}"
    if keyring is not None:
        try:
            val = keyring.get_password(service, ref)
            if val:
                return val
        except Exception:
            pass  # keyring backend unavailable -> fall through to file
    env_path = paths.secrets_env_path(user, data_root)
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().lower() == ref.strip().lower():
                return v.strip().strip('"').strip("'")
    return _secret_from_env(ref)


def set_secret(user: str, ref: str, value: str, data_root=None) -> str:
    """Store a secret in the keyring if available, else append to secrets.env. Returns backend used."""
    service = f"{KEYRING_SERVICE}:{paths.safe_user(user)}"
    if keyring is not None:
        try:
            keyring.set_password(service, ref, value)
            return "keyring"
        except Exception:
            pass
    # Fallback: gitignored secrets.env in the user's data folder (0600 where supported).
    env_path = paths.secrets_env_path(user, data_root)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip().upper()] = v
    existing[ref.strip().upper()] = value
    body = "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n"
    env_path.write_text(body, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return "secrets.env"


def validate(cfg: Config) -> Config:
    """Check required fields; raise ConfigError with an actionable message if invalid."""
    problems = []
    if not cfg.get("search.titles"):
        problems.append("search.titles is empty — add at least one job title to search for.")
    tpw = cfg.get("targets_per_week", 3)
    if not isinstance(tpw, int) or tpw < 1:
        problems.append("targets_per_week must be a positive integer (ESD minimum is 3).")
    if cfg.email_enabled and not cfg.email_to:
        problems.append("email.enabled is true but email.to is empty.")
    li = cfg.get("profile.linkedin_import", "resume")
    if li not in ("resume", "export", "authed"):
        problems.append("profile.linkedin_import must be one of: resume, export, authed.")
    comp_min = cfg.get("search.comp_min")
    if comp_min is not None and not isinstance(comp_min, (int, float)):
        problems.append("search.comp_min must be a number (or left unset).")
    if problems:
        raise ConfigError("Config problems:\n  - " + "\n  - ".join(problems))
    return cfg


def load(user: str, data_root: str | os.PathLike | None = None,
         path: str | os.PathLike | None = None, do_validate: bool = True) -> Config:
    """Load and validate a user's config from their Desktop data folder (or an explicit path)."""
    cfg_path = Path(path) if path else paths.config_path(user, data_root)
    if not cfg_path.exists():
        raise ConfigError(
            f"No config for user {user!r} at {cfg_path}. "
            f"Run: python scripts/setup_user.py --user {user}"
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{cfg_path} did not parse to a mapping.")
    merged = _deep_merge(DEFAULTS, raw)
    merged.setdefault("user", user)
    cfg = Config(user, merged, data_root=data_root)
    return validate(cfg) if do_validate else cfg
