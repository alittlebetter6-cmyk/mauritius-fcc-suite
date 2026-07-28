"""
AI analyst engine.

Wraps the Anthropic API to provide grounded, Mauritius-specific financial-crime
analysis: red-flag review, typology detection, STR narrative drafting, and
plain-language legislation Q&A. The engine injects the curated legislation
corpus as context so answers are anchored to the local framework rather than
generic AML boilerplate.

Requires an Anthropic API key (env var ANTHROPIC_API_KEY) and the `anthropic`
package. If neither is present, callers should fall back to the deterministic
modules (screening, risk, legislation) which work fully offline.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import legislation

DEFAULT_MODEL = os.environ.get("FCC_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """You are the analyst engine inside a Mauritian financial-crime \
compliance tool, used by compliance officers, MLROs and FCC/FSC/FIU/bank staff.

Operating rules:
- Ground every answer in the Mauritian AML/CFT/CPF framework provided below. \
Cite the specific Act and section where relevant (e.g. "FIAMLA s.18", "AMLA 2026 CPF pillar").
- Distinguish the three pillars clearly: Money Laundering (ML), Terrorist \
Financing (TF), and — under AMLA 2026 — Countering Proliferation Financing (CPF).
- Be precise and practitioner-grade. No hedging filler. Use the vocabulary of \
the FSC AML/CFT Handbook and FATF Recommendations.
- You provide decision-support, not legal advice or a final determination. \
Flag where a human MLRO decision, EDD, or a formal STR to the FIU is required.
- When drafting an STR narrative, be factual, chronological and objective; \
never speculate beyond the facts supplied.
- If information is insufficient, state what additional CDD/EDD is needed.

Curated Mauritian legislation context (practitioner metadata, verify against \
the Gazette):
%(corpus)s
"""


def _corpus_text() -> str:
    acts = legislation.acts()
    lines = []
    for a in acts:
        lines.append(f"### {a['short']} — {a['title']} [{a['status']}]")
        lines.append(a.get("summary", ""))
        for s in a.get("key_sections", []) or []:
            lines.append(f"  - {s['ref']}: {s['topic']} — {s['note']}")
        obs = a.get("obligations", []) or []
        if obs:
            lines.append("  Obligations: " + "; ".join(obs))
    return "\n".join(lines)


def _get_api_key() -> str | None:
    """
    Look for the API key in the environment first (works locally and covers
    Streamlit Cloud's default behaviour of exposing top-level secrets as env
    vars), then fall back to st.secrets directly so it also works if the key
    was nested under a section, or on a host that doesn't mirror secrets to
    the environment.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None


def is_available() -> bool:
    if not _get_api_key():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _client():
    import anthropic
    key = _get_api_key()
    return anthropic.Anthropic(api_key=key)


@dataclass
class AIResult:
    text: str
    model: str
    ok: bool
    error: str | None = None


def ask(prompt: str, *, model: str | None = None, max_tokens: int = 1500,
        temperature: float = 0.2) -> AIResult:
    """Single-turn grounded query."""
    model = model or DEFAULT_MODEL
    if not is_available():
        return AIResult("", model, False,
                        "AI engine unavailable: set ANTHROPIC_API_KEY and "
                        "`pip install anthropic`. Deterministic modules still work.")
    try:
        client = _client()
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=SYSTEM_PROMPT % {"corpus": _corpus_text()},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return AIResult(text, model, True)
    except Exception as e:
        return AIResult("", model, False, str(e))


# ---- Task-specific helpers -------------------------------------------------

def analyse_red_flags(fact_pattern: str, **kw) -> AIResult:
    prompt = (
        "Review the following client / transaction fact pattern for financial-"
        "crime red flags. Identify indicators across ML, TF and CPF pillars, "
        "map each to the relevant Mauritian provision, assess overall risk, and "
        "recommend next steps (CDD/EDD, escalation, STR to FIU if warranted).\n\n"
        f"FACT PATTERN:\n{fact_pattern}"
    )
    return ask(prompt, **kw)


def draft_str_narrative(facts: str, **kw) -> AIResult:
    prompt = (
        "Draft a factual, objective Suspicious Transaction Report (STR) narrative "
        "for submission to the Mauritius FIU under FIAMLA s.18. Structure: (1) "
        "subject & account details placeholders, (2) chronological account of the "
        "activity, (3) why it is suspicious (ground in typologies), (4) grounds "
        "for suspicion / provisions engaged. Use bracketed placeholders for data "
        "not supplied. Do not speculate beyond the facts.\n\n"
        f"FACTS:\n{facts}"
    )
    return ask(prompt, max_tokens=2000, **kw)


def explain_provision(question: str, **kw) -> AIResult:
    prompt = (
        "Answer this Mauritian AML/CFT/CPF compliance question in plain, precise "
        "language for a compliance officer. Cite the specific Act/section.\n\n"
        f"QUESTION: {question}"
    )
    return ask(prompt, **kw)


def detect_typology(narrative: str, **kw) -> AIResult:
    prompt = (
        "Classify the likely money-laundering / TF / PF typology in the narrative "
        "below (e.g. trade-based ML, layering via shell companies, procurement "
        "fraud, proliferation-financing evasion, PEP corruption proceeds). Explain "
        "the stage (placement/layering/integration) and matching FATF indicators.\n\n"
        f"NARRATIVE:\n{narrative}"
    )
    return ask(prompt, **kw)
