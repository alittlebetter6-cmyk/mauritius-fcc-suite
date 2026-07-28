"""
Name-matching utilities for sanctions / watchlist screening.

Screening is deliberately conservative: it favours recall (catching possible
matches) over precision, because a missed true match is a regulatory failure
while a false positive is only an analyst review. Every automated score is a
*decision-support* signal — a human must adjudicate.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz

# Common corporate suffixes stripped before comparison so
# "Alpha Trading Ltd" and "Alpha Trading Limited" align.
_CORP_SUFFIXES = {
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "co", "company", "plc", "gmbh", "sa", "sarl", "fze", "fzco", "pte",
    "llp", "lp", "holdings", "group", "trust", "fund", "spc", "gbc",
}

# Very light transliteration/spelling-variant folding for common name forms.
_TRANSLIT = {
    "ph": "f", "ck": "k", "kh": "k", "gh": "g", "ov": "off",
}


def normalise(text: str) -> str:
    """Lower-case, strip accents, collapse whitespace and punctuation."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_corp_suffixes(name: str) -> str:
    tokens = [t for t in name.split() if t not in _CORP_SUFFIXES]
    return " ".join(tokens) if tokens else name


def _fold_variants(name: str) -> str:
    for a, b in _TRANSLIT.items():
        name = name.replace(a, b)
    return name


def prepare(name: str, is_entity: bool = False) -> str:
    n = normalise(name)
    if is_entity:
        n = _strip_corp_suffixes(n)
    return _fold_variants(n)


def similarity(query: str, candidate: str, is_entity: bool = False) -> float:
    """
    Composite 0-100 similarity. Blends token-set ratio (robust to word order
    and extra tokens) with a straight ratio, taking the stronger signal.
    """
    q = prepare(query, is_entity)
    c = prepare(candidate, is_entity)
    if not q or not c:
        return 0.0
    token = fuzz.token_set_ratio(q, c)
    straight = fuzz.ratio(q, c)
    partial = fuzz.partial_ratio(q, c)
    # Weight token_set highest; it best handles "Ivan Volkov" vs "Ivan P. Volkov".
    return round(max(token, 0.6 * token + 0.25 * straight + 0.15 * partial), 1)


@dataclass
class ScreenHit:
    query: str
    matched_name: str
    score: float
    record: dict
    matched_field: str = "name"


@dataclass
class ScreenResult:
    query: str
    hits: list[ScreenHit] = field(default_factory=list)

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    @property
    def band(self) -> str:
        s = self.top_score
        if s >= 92:
            return "STRONG"
        if s >= 82:
            return "PROBABLE"
        if s >= 72:
            return "POSSIBLE"
        return "CLEAR"


def screen_name(
    query: str,
    records: Iterable[dict],
    is_entity: bool = False,
    threshold: float = 72.0,
    max_hits: int = 25,
) -> ScreenResult:
    """Screen a single name against an iterable of sanction records."""
    result = ScreenResult(query=query)
    for rec in records:
        names = [rec.get("name", "")] + list(rec.get("aka", []) or [])
        best_local = 0.0
        best_name = ""
        best_field = "name"
        for i, nm in enumerate(names):
            if not nm:
                continue
            sc = similarity(query, nm, is_entity=is_entity)
            if sc > best_local:
                best_local = sc
                best_name = nm
                best_field = "name" if i == 0 else "aka"
        if best_local >= threshold:
            result.hits.append(
                ScreenHit(query=query, matched_name=best_name, score=best_local,
                          record=rec, matched_field=best_field)
            )
    result.hits.sort(key=lambda h: h.score, reverse=True)
    result.hits = result.hits[:max_hits]
    return result
