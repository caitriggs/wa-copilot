"""
LLM-written cover letters (Anthropic API).
==========================================

Turns the templated draft into a genuinely tailored letter by giving a small Claude model the
user's résumé/history + the specific posting and asking for a concise, human cover letter that
says why they're a strong fit AND why they want the role.

Off by default. Enable per user with `draft.llm.enabled: true` and store the API key:
    python scripts/setup_user.py --user <you> --set-secret anthropic

Model: defaults to Haiku 4.5 (cheap, fast); set `draft.llm.model: "claude-sonnet-5"` for higher
quality. Uses the official `anthropic` SDK. If the package or key is missing, or the call fails,
generate_cover_letter() returns None and the caller falls back to the built-in template.

Guardrail: the prompt forbids inventing employers, titles, dates, metrics, or skills — it may
only use facts present in the résumé/history, keeping the letter (and any resulting application)
truthful.
"""

from __future__ import annotations

from pathlib import Path

from . import paths
from .config import Config
from .models import JobPosting

MODEL_DEFAULT = "claude-sonnet-5"      # latest Sonnet; override with draft.llm.model
_CACHE: dict = {}                       # (user, dedup_key) -> letter, so draft+apply don't double-call

# Models we explicitly turn thinking OFF for (they'd otherwise run adaptive thinking, eating the
# output budget on a task that doesn't need it). Others simply omit the param (no thinking).
_DISABLE_THINKING_PREFIXES = ("claude-sonnet-5", "claude-opus-4")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_NAME = "cover_letter_prompt.md"

_BUILTIN_SYSTEM = (
    "You are an expert cover-letter writer helping {name} land interviews. Write a specific, "
    "compelling letter that makes the hiring manager want to schedule an intro call. Use ONLY "
    "facts in the candidate profile — do not invent employers, titles, dates, metrics, or skills. "
    "Open with a real hook (not 'I am writing to apply'), weave the candidate's most relevant "
    "achievements together with the posting's requirements and their current weekly focus to show "
    "synergy, say why THIS company/role, keep a warm human voice with no clichés, and close with a "
    "clear call to action. About {max_words} words, 3-4 short paragraphs. Output ONLY the letter."
)


def status(cfg: Config) -> tuple[bool, str]:
    """Return (available, reason). `reason` explains why it's off when not available."""
    if not cfg.get("draft.llm.enabled", False):
        return (False, "draft.llm.enabled is false in config.yaml (add the draft block + set it true)")
    if not cfg.get_secret("anthropic"):
        return (False, "no Anthropic API key stored — run: setup_user.py --user "
                       f"{cfg.user} --set-secret anthropic")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return (False, "anthropic package not installed — run: pip install anthropic")
    return (True, "")


def available(cfg: Config) -> bool:
    """True if LLM drafting is enabled, the SDK is installed, and an API key is stored."""
    return status(cfg)[0]


def has_api(cfg: Config) -> bool:
    """True if an Anthropic key + SDK are present (independent of draft.llm.enabled).

    Used by features that can use the LLM whenever a key exists (e.g. weekly-focus planning).
    """
    if not cfg.get_secret("anthropic"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _profile_text(history: dict) -> str:
    """A compact, factual profile blob for the prompt (capped to control token cost)."""
    if history.get("raw_text"):
        return history["raw_text"][:6000]
    parts = []
    if history.get("headline"):
        parts.append(f"Headline: {history['headline']}")
    if history.get("summary"):
        parts.append(f"Summary: {history['summary']}")
    for r in (history.get("roles", []) or [])[:6]:
        line = f"- {r.get('title','')}".rstrip()
        if r.get("company"):
            line += f" at {r['company']}"
        span = " ".join(x for x in (r.get("start", ""), r.get("end", "")) if x)
        if span:
            line += f" ({span})"
        parts.append(line)
        for b in (r.get("bullets", []) or [])[:4]:
            parts.append(f"    • {b}")
    if history.get("skills"):
        parts.append("Skills: " + ", ".join(history["skills"][:25]))
    if history.get("highlights"):
        parts.append("Highlights:")
        parts += [f"    • {h}" for h in history["highlights"][:6]]
    return "\n".join(parts)[:6000]


def _load_system_template(cfg: Config) -> str:
    """The editable instruction template: the user's copy overrides the repo default."""
    candidates = []
    try:
        candidates.append(paths.user_root(cfg.user, cfg.data_root) / _TEMPLATE_NAME)
    except Exception:
        pass
    candidates.append(_REPO_ROOT / "config" / _TEMPLATE_NAME)
    for c in candidates:
        try:
            if c.exists():
                text = c.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:
            continue
    return _BUILTIN_SYSTEM


def build_prompt(cfg: Config, jp: JobPosting, history: dict) -> tuple[str, str]:
    """Return (system, user) prompt strings. Pure — unit-tested without the network."""
    name = str(cfg.get("display_name", cfg.user))
    max_words = int(cfg.get("draft.llm.max_words", 300))
    notes = cfg.weekly_notes

    system = (_load_system_template(cfg)
              .replace("{name}", name)
              .replace("{max_words}", str(max_words)))

    comp = ""
    if jp.comp_min or jp.comp_max:
        comp = f"Pay range: {jp.comp_min or '?'}–{jp.comp_max or '?'}\n"
    user = (
        "# The role\n"
        f"Title: {jp.title}\n"
        f"Company: {jp.employer or '(unknown)'}\n"
        f"Location: {jp.location or '(n/a)'}\n"
        f"{comp}"
        f"Posting:\n{(jp.description or '(no description provided)')[:4000]}\n\n"
        f"# {name}'s background — the ONLY facts you may use\n"
        f"{_profile_text(history)}\n\n"
        + (f"# {name}'s focus this week (use as evidence of current motivation/direction)\n{notes}\n\n"
           if notes.strip() else "")
        + f"Write {name}'s cover letter now. Make the connection between their background, this "
        "specific posting, and their current focus vivid and concrete — the kind of letter that "
        f"makes a hiring manager want to schedule an intro call. Sign it \"{name}\"."
    )
    return system, user


def _make_client(anthropic_mod, api_key: str):
    """Build the Anthropic client, working around a broken SSL_CERT_FILE (common under conda).

    Some environments export SSL_CERT_FILE / SSL_CERT_DIR pointing at a path that no longer
    exists, so httpx raises FileNotFoundError building its TLS context. Pinning verification to
    certifi's bundle sidesteps that; we fall back to the default client if certifi isn't available.
    """
    try:
        import certifi
        return anthropic_mod.Anthropic(
            api_key=api_key,
            http_client=anthropic_mod.DefaultHttpxClient(verify=certifi.where()),
        )
    except Exception:
        return anthropic_mod.Anthropic(api_key=api_key)


def generate_cover_letter(cfg: Config, jp: JobPosting, history: dict) -> str | None:
    """Generate a tailored cover letter, or None on any problem (caller falls back to template)."""
    cache_key = (cfg.user, jp.dedup_key)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        import anthropic
    except ImportError:
        return None
    api_key = cfg.get_secret("anthropic")
    if not api_key:
        return None

    model = cfg.get("draft.llm.model", MODEL_DEFAULT)
    disable_thinking = any(model.startswith(p) for p in _DISABLE_THINKING_PREFIXES)
    try:
        system, user = build_prompt(cfg, jp, history)
        kwargs = dict(model=model, max_tokens=2000, system=system,
                      messages=[{"role": "user", "content": user}], timeout=60)
        # Turn thinking off where the model would otherwise run adaptive thinking and spend the
        # whole output budget before writing the letter. Other models simply omit the param.
        if disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}
        client = _make_client(anthropic, api_key)
        resp = client.messages.create(**kwargs)
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        if not text:
            stop = getattr(resp, "stop_reason", "?")
            print(f"  [llm] {model} returned no letter text (stop_reason={stop}). "
                  "If stop_reason=max_tokens, thinking is eating the budget — try model "
                  "claude-haiku-4-5, or upgrade the SDK: pip install -U anthropic.")
            _CACHE[cache_key] = None
            return None
    except Exception as e:  # noqa: BLE001 — SDK error strings don't contain the API key
        print(f"  [llm] cover-letter generation failed: {type(e).__name__}: {str(e)[:300]}")
        _CACHE[cache_key] = None
        return None

    _CACHE[cache_key] = text
    return text
