"""
Client / entity risk-scoring engine.

A transparent, weighted, rules-based model aligned to the FSC AML/CFT Handbook
risk-factor categories and the AMLA 2026 three-pillar model (ML / TF / CPF).
Every score is explainable — the engine returns the exact factors that drove
the rating, which is what an FSC inspection expects to see documented.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# High-risk jurisdiction cues (illustrative — align to FATF lists in production:
# FATF "black" and "grey" lists, plus your own EDD country policy).
FATF_CALL_FOR_ACTION = {"KP", "IR", "MM"}          # highest risk
FATF_INCREASED_MONITORING = {"BF", "CM", "HR", "CD", "HT", "KE", "ML", "MZ",
                             "NA", "NG", "ZA", "SS", "SY", "VE", "YE"}
# Note: grey/black lists change at FATF plenaries — refresh this set.

CPF_PROLIFERATION_NEXUS = {"KP", "IR", "SY"}       # AMLA 2026 CPF pillar cue

WEIGHTS = {
    "country": 25,
    "pep": 20,
    "sanctions_hit": 30,   # dominant — a probable match should force review
    "product": 10,
    "delivery": 8,
    "adverse_media": 12,
    "cash_intensive": 8,
    "complex_structure": 12,
    "cpf_nexus": 15,
}


@dataclass
class RiskFactor:
    code: str
    label: str
    points: float
    rationale: str


@dataclass
class RiskResult:
    score: float
    band: str
    pillar_flags: list[str]
    factors: list[RiskFactor] = field(default_factory=list)

    @property
    def edd_required(self) -> bool:
        return self.band in ("HIGH", "PROHIBITED")


def _band(score: float, has_sanction_strong: bool) -> str:
    if has_sanction_strong:
        return "PROHIBITED"
    if score >= 65:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def assess(
    *,
    country: str = "",
    is_pep: bool = False,
    sanctions_band: str = "CLEAR",   # from matching.ScreenResult.band
    product_risk: str = "standard",  # standard | high
    delivery_nonface: bool = False,
    adverse_media: bool = False,
    cash_intensive: bool = False,
    complex_structure: bool = False,
) -> RiskResult:
    factors: list[RiskFactor] = []
    pillars: set[str] = set()
    country = (country or "").upper()

    # Country / geographic risk (ML + potential TF)
    if country in FATF_CALL_FOR_ACTION:
        factors.append(RiskFactor("country", "Country risk",
                       WEIGHTS["country"],
                       f"{country} is a FATF call-for-action jurisdiction."))
        pillars.update({"ML", "TF"})
    elif country in FATF_INCREASED_MONITORING:
        factors.append(RiskFactor("country", "Country risk",
                       WEIGHTS["country"] * 0.6,
                       f"{country} is under FATF increased monitoring."))
        pillars.add("ML")

    # CPF proliferation nexus (AMLA 2026 third pillar)
    if country in CPF_PROLIFERATION_NEXUS:
        factors.append(RiskFactor("cpf_nexus", "Proliferation-financing nexus",
                       WEIGHTS["cpf_nexus"],
                       f"{country} carries proliferation-financing exposure — "
                       "AMLA 2026 requires a distinct CPF assessment."))
        pillars.add("CPF")

    if is_pep:
        factors.append(RiskFactor("pep", "PEP exposure", WEIGHTS["pep"],
                       "Politically Exposed Person — mandatory EDD and senior "
                       "sign-off."))
        pillars.add("ML")

    # Sanctions screening outcome
    band_points = {"STRONG": WEIGHTS["sanctions_hit"],
                   "PROBABLE": WEIGHTS["sanctions_hit"] * 0.8,
                   "POSSIBLE": WEIGHTS["sanctions_hit"] * 0.4,
                   "CLEAR": 0.0}
    sp = band_points.get(sanctions_band, 0.0)
    if sp > 0:
        factors.append(RiskFactor("sanctions", f"Sanctions screening: {sanctions_band}",
                       sp, "Potential sanctions/watchlist match requires "
                       "adjudication before onboarding."))
        pillars.update({"ML", "TF"})

    if product_risk == "high":
        factors.append(RiskFactor("product", "Product/service risk",
                       WEIGHTS["product"], "Higher-risk product (e.g. bearer "
                       "instruments, private banking, VASP activity)."))
    if delivery_nonface:
        factors.append(RiskFactor("delivery", "Non-face-to-face onboarding",
                       WEIGHTS["delivery"], "Remote onboarding elevates "
                       "impersonation risk."))
    if adverse_media:
        factors.append(RiskFactor("adverse_media", "Adverse media",
                       WEIGHTS["adverse_media"], "Negative news requires "
                       "verification and documentation."))
        pillars.add("ML")
    if cash_intensive:
        factors.append(RiskFactor("cash", "Cash-intensive business",
                       WEIGHTS["cash_intensive"], "Cash intensity raises "
                       "placement risk."))
    if complex_structure:
        factors.append(RiskFactor("structure", "Complex/opaque structure",
                       WEIGHTS["complex_structure"], "Layered ownership obscures "
                       "UBO — scrutinise against expanded AMLA 2026 BO definition."))
        pillars.add("ML")

    raw = sum(f.points for f in factors)
    score = round(min(raw, 100.0), 1)
    has_strong = sanctions_band == "STRONG"
    band = _band(score, has_strong)

    order = {"ML": 0, "TF": 1, "CPF": 2}
    pillar_flags = sorted(pillars, key=lambda p: order.get(p, 9))
    return RiskResult(score=score, band=band, pillar_flags=pillar_flags,
                      factors=factors)
