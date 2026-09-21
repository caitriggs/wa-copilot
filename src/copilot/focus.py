"""
Weekly focus — turn `search.weekly_notes` into the week's search plan (an OVERRIDE, not a boost).
================================================================================================

When you put a note like "I'd like to explore State jobs with outdoor/remote flexibility" or
"building a cute robot dog army to collect ad data", that should STEER the whole week: change what
gets searched, relax the title/skill/salary matching so plausible-stretch roles surface, and let
the cover letter do the heavy lifting connecting your background to a non-obvious target.

`plan(cfg)` returns a FocusPlan (or None when weekly_notes is empty / focus disabled):
  - queries        : job-title/keyword phrases to search sources with (override your usual titles)
  - keywords       : ranking terms that signal a match to the focus
  - sectors/companies: optional target sectors / employers
  - relax_title    : true -> don't require the posting title to match your past titles
  - relax_comp     : true -> flex the salary floor for this focus

If an Anthropic key is configured, the plan is derived by the LLM (it can infer "robot dog + ad
data" -> marketing analytics + robotics companies + quirky cultures). Otherwise a simple keyword
plan is used. Discovery/ranking (discover.py) consume the plan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import Config

_CACHE: dict = {}


@dataclass
class FocusPlan:
    queries: list
    keywords: list
    sectors: list = field(default_factory=list)
    companies: list = field(default_factory=list)
    relax_title: bool = True
    relax_comp: bool = True
    summary: str = ""


def _kw_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9]+", (text or "").lower()) if len(t) > 3]


def _heuristic_plan(notes: str) -> FocusPlan:
    toks = list(dict.fromkeys(_kw_tokens(notes)))
    return FocusPlan(queries=[notes.strip()[:80]], keywords=toks[:12],
                     relax_title=True, relax_comp=True, summary=notes.strip()[:120])


def _llm_plan(cfg: Config, notes: str) -> FocusPlan | None:
    from . import llm
    if not llm.has_api(cfg):
        return None
    try:
        import anthropic
        client = llm._make_client(anthropic, cfg.get_secret("anthropic"))
        titles = ", ".join(cfg.get("search.titles", []) or [])
        locs = ", ".join(cfg.get("search.locations", []) or [])
        system = (
            "Turn a job seeker's free-text weekly focus into a concrete job-search plan. The focus "
            "OVERRIDES their usual titles — bias the plan toward the focus even when it's a pivot or "
            "stretch from their background. Return ONLY minified JSON with keys: queries (3-6 short "
            "job-title/keyword search phrases to find matching postings), keywords (ranking terms "
            "that signal a match), sectors (list), companies (optional target employers), relax_title "
            "(bool: true if the focus implies roles unlike their past titles), relax_comp (bool: true "
            "if they'd flex on salary for this focus), summary (one short line). No prose, JSON only."
        )
        user = (f"Weekly focus: {notes}\n\nTheir usual titles: {titles or '(none)'}\n"
                f"Preferred locations: {locs or '(any)'}\n\nReturn the JSON plan.")
        model = cfg.get("draft.llm.model", "claude-sonnet-5")
        kwargs = dict(model=model, max_tokens=700, system=system,
                      messages=[{"role": "user", "content": user}])
        if model.startswith(("claude-sonnet-5", "claude-opus-4")):
            kwargs["thinking"] = {"type": "disabled"}
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        d = json.loads(text)
        return FocusPlan(
            queries=[str(x) for x in (d.get("queries") or [])][:6] or [notes[:80]],
            keywords=[str(x) for x in (d.get("keywords") or [])][:15],
            sectors=[str(x) for x in (d.get("sectors") or [])][:8],
            companies=[str(x) for x in (d.get("companies") or [])][:12],
            relax_title=bool(d.get("relax_title", True)),
            relax_comp=bool(d.get("relax_comp", True)),
            summary=str(d.get("summary", notes[:120])),
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [focus] LLM plan failed ({type(e).__name__}); using a simple keyword plan.")
        return None


def plan(cfg: Config) -> FocusPlan | None:
    """Return this week's FocusPlan, or None if there's no weekly note / focus is disabled."""
    if not cfg.get("search.focus.enabled", True):
        return None
    notes = cfg.weekly_notes
    if not notes:
        return None
    cache_key = (cfg.user, notes)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    p = _llm_plan(cfg, notes) or _heuristic_plan(notes)
    _CACHE[cache_key] = p
    print(f"  [focus] week steered by your note: {p.summary!r}")
    if p.queries:
        print(f"  [focus] searching: {', '.join(p.queries)}"
              + ("  (title/salary relaxed)" if (p.relax_title or p.relax_comp) else ""))
    return p
