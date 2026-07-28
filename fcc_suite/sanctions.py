"""
Sanctions & watchlist data layer.

Design goals
------------
* Pull authoritative public lists (UN, OFAC, UK OFSI) on demand and cache them.
* Degrade gracefully offline: if a live fetch fails, fall back to the last
  cached copy, and if none exists, to the bundled demo dataset — so the app
  always runs, while never silently pretending demo data is real.
* Keep every record in one normalised shape for the screening engine.

IMPORTANT (production): the EU consolidated list now requires an access token,
and Mauritius's National Sanctions Secretariat list is published separately by
the NSS — wire those in with your credentials/URL in SOURCES below.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

try:
    import requests
except Exception:  # requests optional until first live fetch
    requests = None

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / "sanctions_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Authoritative public sources. Verify URLs periodically — publishers move them.
SOURCES = {
    "UN": {
        "label": "UN Security Council Consolidated List",
        "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        "kind": "un_xml",
    },
    "OFAC": {
        "label": "US OFAC SDN List",
        "url": "https://www.treasury.gov/ofac/downloads/sdn.csv",
        "kind": "ofac_csv",
    },
    "UK": {
        "label": "UK OFSI Consolidated List",
        "url": "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv",
        "kind": "uk_csv",
    },
    # Placeholders to be completed with your access:
    # "EU": {"label": "EU Consolidated List", "url": "<tokenised EU url>", "kind": "eu_xml"},
    # "MU_NSS": {"label": "Mauritius National Sanctions List", "url": "<NSS url>", "kind": "mu_csv"},
}

CACHE_TTL = 24 * 3600  # refresh at most once/day by default


@dataclass
class ListStatus:
    code: str
    label: str
    count: int
    source: str  # "live", "cache", or "demo"
    fetched_at: float | None = None
    error: str | None = None


def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.json"


def _write_cache(code: str, records: list[dict]) -> None:
    payload = {"fetched_at": time.time(), "records": records}
    _cache_path(code).write_text(json.dumps(payload))


def _read_cache(code: str) -> dict | None:
    p = _cache_path(code)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


# ---- Parsers: each returns a list of normalised records -------------------

def _parse_un_xml(raw: bytes) -> list[dict]:
    out: list[dict] = []
    root = ET.fromstring(raw)
    # Individuals
    for ind in root.iter("INDIVIDUAL"):
        first = (ind.findtext("FIRST_NAME") or "").strip()
        second = (ind.findtext("SECOND_NAME") or "").strip()
        third = (ind.findtext("THIRD_NAME") or "").strip()
        name = " ".join(p for p in [first, second, third] if p)
        aka = [a.findtext("ALIAS_NAME", "").strip()
               for a in ind.iter("INDIVIDUAL_ALIAS") if a.findtext("ALIAS_NAME")]
        out.append({"name": name, "type": "individual", "list": "UN",
                    "program": (ind.findtext("UN_LIST_TYPE") or "").strip(),
                    "aka": [a for a in aka if a]})
    for ent in root.iter("ENTITY"):
        name = (ent.findtext("FIRST_NAME") or "").strip()
        aka = [a.findtext("ALIAS_NAME", "").strip()
               for a in ent.iter("ENTITY_ALIAS") if a.findtext("ALIAS_NAME")]
        out.append({"name": name, "type": "entity", "list": "UN",
                    "program": (ent.findtext("UN_LIST_TYPE") or "").strip(),
                    "aka": [a for a in aka if a]})
    return [r for r in out if r["name"]]


def _parse_ofac_csv(raw: bytes) -> list[dict]:
    out: list[dict] = []
    text = raw.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 3:
            continue
        # OFAC sdn.csv: ent_num, SDN_Name, SDN_Type, Program, ...
        name = row[1].strip().strip('"')
        sdn_type = row[2].strip().strip('"')
        program = row[3].strip().strip('"') if len(row) > 3 else ""
        if not name or name == "-0-":
            continue
        out.append({"name": name,
                    "type": "individual" if sdn_type.lower() == "individual" else "entity",
                    "list": "OFAC", "program": program, "aka": []})
    return out


def _parse_uk_csv(raw: bytes) -> list[dict]:
    out: list[dict] = []
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # OFSI file has a title line before the header; find the header row.
    start = 0
    for i, ln in enumerate(lines[:5]):
        if "Name 1" in ln or "Group Type" in ln:
            start = i
            break
    reader = csv.DictReader(lines[start:])
    for row in reader:
        parts = [row.get(f"Name {i}", "") for i in range(1, 7)]
        name = " ".join(p.strip() for p in parts if p and p.strip())
        gtype = (row.get("Group Type") or "").strip().lower()
        if not name:
            continue
        out.append({"name": name,
                    "type": "individual" if "individual" in gtype else "entity",
                    "list": "UK", "program": (row.get("Regime") or "").strip(),
                    "aka": []})
    return out


_PARSERS = {
    "un_xml": _parse_un_xml,
    "ofac_csv": _parse_ofac_csv,
    "uk_csv": _parse_uk_csv,
}


def fetch_list(code: str, force: bool = False, timeout: int = 30) -> tuple[list[dict], ListStatus]:
    """Fetch one list, using cache within TTL. Returns (records, status)."""
    src = SOURCES.get(code)
    if not src:
        return [], ListStatus(code, code, 0, "demo", error="unknown source")

    cache = _read_cache(code)
    if cache and not force and (time.time() - cache["fetched_at"] < CACHE_TTL):
        recs = cache["records"]
        return recs, ListStatus(code, src["label"], len(recs), "cache",
                                 cache["fetched_at"])

    if requests is None:
        if cache:
            recs = cache["records"]
            return recs, ListStatus(code, src["label"], len(recs), "cache",
                                     cache["fetched_at"], error="requests not installed")
        return [], ListStatus(code, src["label"], 0, "demo", error="requests not installed")

    try:
        resp = requests.get(src["url"], timeout=timeout,
                            headers={"User-Agent": "MauritiusFCCSuite/0.1"})
        resp.raise_for_status()
        records = _PARSERS[src["kind"]](resp.content)
        _write_cache(code, records)
        return records, ListStatus(code, src["label"], len(records), "live", time.time())
    except Exception as e:  # graceful fallback to cache
        if cache:
            recs = cache["records"]
            return recs, ListStatus(code, src["label"], len(recs), "cache",
                                     cache["fetched_at"], error=str(e))
        return [], ListStatus(code, src["label"], 0, "demo", error=str(e))


def load_demo() -> list[dict]:
    data = json.loads((DATA_DIR / "sample_sanctions.json").read_text())
    return data["entries"]


# ---- Mauritius domestic list (NSSec) — analyst-maintained register ---------
# The domestic list is published as NSSec public notices / Gazette entries, not
# a machine feed, so it is maintained locally with provenance and screened like
# any other source. Screening against UN + this domestic list is a LEGAL
# requirement under the UN Sanctions Act 2019 (s.25).

MU_NSS_FILE = DATA_DIR / "mu_nss.json"


def load_mu_nss(include_sample: bool = False) -> list[dict]:
    if not MU_NSS_FILE.exists():
        return []
    data = json.loads(MU_NSS_FILE.read_text())
    entries = data.get("entries", [])
    if not include_sample:
        entries = [e for e in entries if e.get("added_by") != "sample"]
    return entries


def mu_nss_meta() -> dict:
    if not MU_NSS_FILE.exists():
        return {}
    return json.loads(MU_NSS_FILE.read_text()).get("_meta", {})


def add_mu_nss_entry(entry: dict) -> None:
    """Append a domestic designation with provenance and re-save the register."""
    data = json.loads(MU_NSS_FILE.read_text()) if MU_NSS_FILE.exists() else \
        {"_meta": {}, "entries": []}
    entry.setdefault("list", "MU-NSS")
    entry.setdefault("aka", [])
    data["entries"] = [e for e in data.get("entries", []) if e.get("added_by") != "sample"]
    data["entries"].append(entry)
