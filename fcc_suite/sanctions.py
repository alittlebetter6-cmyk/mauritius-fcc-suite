"""
Sanctions & watchlist data layer.
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
except Exception:
    requests = None

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = Path(os.environ.get("FCC_CACHE", "/tmp/fcc_sanctions_cache"))
try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    CACHE_DIR = Path("/tmp/fcc_sanctions_cache")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
}

CACHE_TTL = 24 * 3600


@dataclass
class ListStatus:
    code: str
    label: str
    count: int
    source: str
    fetched_at: float | None = None
    error: str | None = None


def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.json"


def _write_cache(code: str, records: list[dict]) -> None:
    try:
        payload = {"fetched_at": time.time(), "records": records}
        _cache_path(code).write_text(json.dumps(payload))
    except Exception:
        pass


def _read_cache(code: str) -> dict | None:
    p = _cache_path(code)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _parse_un_xml(raw: bytes) -> list[dict]:
    out: list[dict] = []
    root = ET.fromstring(raw)
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
    except Exception as e:
        if cache:
            recs = cache["records"]
            return recs, ListStatus(code, src["label"], len(recs), "cache",
                                     cache["fetched_at"], error=str(e))
        return [], ListStatus(code, src["label"], 0, "demo", error=str(e))


def load_demo() -> list[dict]:
    data = json.loads((DATA_DIR / "sample_sanctions.json").read_text())
    return data["entries"]


MU_NSS_FILE = DATA_DIR / "mu_nss.json"
_MU_NSS_WRITABLE = Path("/tmp/fcc_mu_nss.json")


def _nss_path() -> Path:
    if _MU_NSS_WRITABLE.exists():
        return _MU_NSS_WRITABLE
    return MU_NSS_FILE


def load_mu_nss(include_sample: bool = False) -> list[dict]:
    p = _nss_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    entries = data.get("entries", [])
    if not include_sample:
        entries = [e for e in entries if e.get("added_by") != "sample"]
    return entries


def mu_nss_meta() -> dict:
    p = _nss_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("_meta", {})
    except Exception:
        return {}


def add_mu_nss_entry(entry: dict) -> None:
    p = _nss_path()
    try:
        data = json.loads(p.read_text()) if p.exists() else {"_meta": {}, "entries": []}
    except Exception:
        data = {"_meta": {}, "entries": []}
    entry.setdefault("list", "MU-NSS")
    entry.setdefault("aka", [])
    data["entries"] = [e for e in data.get("entries", []) if e.get("added_by") != "sample"]
    data["entries"].append(entry)
    _MU_NSS_WRITABLE.write_text(json.dumps(data, indent=2))


def load_all(codes: list[str] | None = None, force: bool = False,
             include_domestic: bool = True):
    codes = codes or list(SOURCES.keys())
    all_records: list[dict] = []
    statuses: list[ListStatus] = []
    any_real = False
    for code in codes:
        recs, st = fetch_list(code, force=force)
        statuses.append(st)
        if recs:
            any_real = True
            all_records.extend(recs)

    if include_domestic:
        nss = load_mu_nss()
        if nss:
            any_real = True
            all_records.extend(nss)
            statuses.append(ListStatus("MU-NSS",
                            "Mauritius Domestic List (NSSec)", len(nss), "live"))
        else:
            statuses.append(ListStatus("MU-NSS",
                            "Mauritius Domestic List (NSSec)", 0, "demo",
                            error="no domestic designations entered yet"))

    if not any_real:
        demo = load_demo()
        all_records.extend(demo)
        statuses.append(ListStatus("DEMO", "Bundled demo dataset", len(demo), "demo"))
    return all_records, statuses
