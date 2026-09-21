"""
Funnel + application metrics for the weekly packet.
===================================================

Reads the discovery cache (screened/ranked + top-N ranking reasons) and the ESD log (real
applications) to produce the numbers that go in the claim packet:

  - the funnel: how many postings were screened -> ranked -> applied this week,
  - the top ranked postings with WHY they ranked (reasons), and
  - cumulative application stats: which companies and job types you've applied to so far.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

from . import paths, logbook
from .config import Config


def _load_cache(cfg: Config, week_end: date, data_root) -> dict:
    p = paths.postings_cache_path(cfg.user, week_end, data_root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"postings": data}
    except Exception:
        return {}


def _applied_rows(cfg: Config, data_root) -> list[dict]:
    return [r for r in logbook.read_all(cfg.user, data_root)
            if r.get("activity_type") == "applied_to_job"]


def funnel(cfg: Config, week_end: date, data_root=None) -> dict:
    data_root = data_root if data_root is not None else cfg.data_root
    cache = _load_cache(cfg, week_end, data_root)
    we = week_end.isoformat() if isinstance(week_end, date) else str(week_end)
    applied_week = sum(1 for r in _applied_rows(cfg, data_root)
                       if str(r.get("week_ending")) == we)
    return {
        "screened": cache.get("screened", len(cache.get("postings", []))),
        "ranked": cache.get("ranked", len(cache.get("postings", []))),
        "filtered_out": cache.get("filtered_out", 0),
        "applied_this_week": applied_week,
    }


def top_ranked(cfg: Config, week_end: date, data_root=None, n: int = 3) -> list[dict]:
    data_root = data_root if data_root is not None else cfg.data_root
    posts = _load_cache(cfg, week_end, data_root).get("postings", [])
    out = []
    for p in posts[:n]:
        out.append({
            "title": p.get("title", ""), "employer": p.get("employer", ""),
            "score": p.get("score", 0), "reasons": p.get("reasons", []),
            "url": p.get("url", ""),
        })
    return out


def applied_summary(cfg: Config, data_root=None) -> dict:
    """Cumulative: total applications, companies, and job types (titles) applied to so far."""
    data_root = data_root if data_root is not None else cfg.data_root
    rows = _applied_rows(cfg, data_root)
    companies = Counter(r.get("employer_or_org", "").strip() for r in rows if r.get("employer_or_org", "").strip())
    titles = Counter(r.get("position", "").strip() for r in rows if r.get("position", "").strip())
    return {
        "total_applied": len(rows),
        "companies": companies.most_common(),
        "job_types": titles.most_common(),
    }


def render_markdown(cfg: Config, week_end: date, data_root=None) -> str:
    """The Metrics section for the packet."""
    data_root = data_root if data_root is not None else cfg.data_root
    f = funnel(cfg, week_end, data_root)
    top = top_ranked(cfg, week_end, data_root, n=int(cfg.get("apply.top_n", 3)))
    summ = applied_summary(cfg, data_root)

    lines = ["## Application funnel & metrics\n"]
    lines.append(f"- **Screened this week:** {f['screened']} postings")
    lines.append(f"- **Ranked (passed your filters):** {f['ranked']} "
                 f"({f['filtered_out']} filtered out)")
    lines.append(f"- **Applied this week:** {f['applied_this_week']}")
    lines.append(f"- **Applied all-time:** {summ['total_applied']}\n")

    if top:
        lines.append("### Top-ranked this week — why")
        for i, t in enumerate(top, 1):
            who = f" @ {t['employer']}" if t["employer"] else ""
            lines.append(f"{i}. **{t['title']}{who}** (score {t['score']}) — "
                         f"{'; '.join(t['reasons'])}")
        lines.append("")

    if summ["companies"]:
        lines.append("### Companies applied to so far")
        lines.append(", ".join(f"{c} ({n})" for c, n in summ["companies"][:20]) + "\n")
    if summ["job_types"]:
        lines.append("### Job types applied to so far")
        lines.append(", ".join(f"{t} ({n})" for t, n in summ["job_types"][:20]) + "\n")
    return "\n".join(lines)
