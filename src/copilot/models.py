"""Shared data models.

`JobPosting` is the normalized shape every source returns, so discover/draft/packet can treat
postings uniformly regardless of where they came from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


@dataclass
class JobPosting:
    source: str                     # "usajobs", "worksourcewa", "feeds:linkedin", ...
    title: str
    employer: str
    location: str = ""
    url: str = ""
    remote: bool = False
    comp_min: Optional[float] = None
    comp_max: Optional[float] = None
    comp_source: str = ""           # "posting" | "estimate" | ""
    posted_date: str = ""           # ISO date string when available
    description: str = ""
    keywords: list = field(default_factory=list)
    best_resume: str = ""           # label of the resume this posting best matches (multi-resume)

    @property
    def dedup_key(self) -> str:
        """Stable identity across runs/sources: employer|title|location."""
        basis = f"{_norm(self.employer)}|{_norm(self.title)}|{_norm(self.location)}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dedup_key"] = self.dedup_key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobPosting":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in allowed})
