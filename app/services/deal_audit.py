"""ARVO — DealAuditService + MaterialityService. AI interpreta, Python decide."""

from decimal import Decimal
from app.services.financial import D, money

# Deterministic materiality thresholds (BRL) — Python owns regra
MATERIALITY_THRESHOLDS = {"LOW": Decimal("1000"), "MEDIUM": Decimal("10000"), "HIGH": Decimal("50000")}

def materiality(amount: str) -> tuple[str, str]:
    a = D(amount)
    if a >= MATERIALITY_THRESHOLDS["HIGH"]: return "HIGH", "validated_risk"
    if a >= MATERIALITY_THRESHOLDS["MEDIUM"]: return "MEDIUM", "validated_risk"
    if a >= MATERIALITY_THRESHOLDS["LOW"]: return "LOW", "potential_exposure"
    return "LOW", "potential_exposure"

def prioritize(findings: list[dict]) -> list[dict]:
    sev_order = {"CRITICAL":4,"HIGH":3,"MEDIUM":2,"LOW":1}
    def key(f): return (sev_order.get(f.get("severity","MEDIUM"),2), D(f.get("exposure_amount","0")))
    return sorted(findings, key=key, reverse=True)

async def audit_deal(deal: dict, ai_output: dict | None = None) -> dict:
    """Recebe extração AI (texto interpretado) + calcula exposição determinística."""
    # AI output example: {"risks":[{"kind":"DISCOUNT","discount_pct":15,"reason":"..."}]}
    risks = (ai_output or {}).get("risks", [])
    # Python calculates exposure: discount * gross
    gross = D(deal.get("amount","0"))
    exposure = Decimal("0")
    for r in risks:
        if r.get("kind")=="DISCOUNT":
            pct = D(r.get("discount_pct","0"))/Decimal("100")
            exposure += gross * pct
    exp_s = money(exposure)
    level, bucket = materiality(exp_s)
    return {"deal_id": deal.get("id"), "gross": money(gross), "exposure": exp_s, "materiality": level, "bucket": bucket, "risks": risks, "prioritized": level in ("HIGH","MEDIUM")}
