"""
Mauritian AML/CFT/CPF legislation repository.

Provides structured, searchable access to the framework so an analyst can go
from a fact pattern to the relevant statute/provision quickly. Content is
curated practitioner metadata — not the official Gazette text. Always verify
against the current consolidated Act.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

DATA = Path(__file__).parent / "data" / "legislation.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(DATA.read_text())


def meta() -> dict:
    return _load()["_meta"]


def acts() -> list[dict]:
    return _load()["acts"]


def regulators() -> list[dict]:
    return _load()["regulators"]


def get_act(act_id: str) -> dict | None:
    return next((a for a in acts() if a["id"] == act_id), None)


def _act_haystack(act: dict) -> str:
    parts = [act.get("short", ""), act.get("title", ""), act.get("summary", "")]
    parts += act.get("key_changes", []) or []
    parts += act.get("obligations", []) or []
    parts += [act.get("practitioner_notes", "")]
    for s in act.get("key_sections", []) or []:
        parts += [s.get("ref", ""), s.get("topic", ""), s.get("note", "")]
    return " \n ".join(parts).lower()


@dataclass
class LegHit:
    act: dict
    score: float
    why: str


def search(query: str, limit: int = 6) -> list[LegHit]:
    """Keyword + fuzzy search across the corpus. Returns ranked acts."""
    q = query.lower().strip()
    if not q:
        return [LegHit(a, 0.0, "") for a in acts()][:limit]
    hits: list[LegHit] = []
    terms = [t for t in q.split() if len(t) > 2]
    for act in acts():
        hay = _act_haystack(act)
        # exact-term coverage
        covered = sum(1 for t in terms if t in hay)
        coverage = (covered / len(terms) * 100) if terms else 0
        fuzzy = fuzz.token_set_ratio(q, hay[:2000])
        score = round(0.7 * coverage + 0.3 * fuzzy, 1)
        # boost direct short-name / section hits
        if q in act.get("short", "").lower() or q in act.get("title", "").lower():
            score = max(score, 95.0)
        for s in act.get("key_sections", []) or []:
            if q.replace(" ", "") in s.get("ref", "").lower().replace(" ", ""):
                score = max(score, 97.0)
        why = ""
        for t in terms:
            if t in hay:
                why = t
                break
        if score > 0:
            hits.append(LegHit(act, score, why))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def obligations_index() -> list[tuple[str, str]]:
    """Flat list of (act short name, obligation) for a checklist view."""
    out: list[tuple[str, str]] = []
    for a in acts():
        for ob in a.get("obligations", []) or []:
            out.append((a["short"], ob))
    return out
