"""ARVO — DealAuditService + MaterialityService. AI interpreta, Python decide."""

import json
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

def parse_ai_risks(content: str) -> list[dict]:
    """Parse LLM JSON into validated risks. Invalid output yields no risks, never invented ones."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.removeprefix("json").strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    raw = data.get("risks", []) if isinstance(data, dict) else []
    risks = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        pct = D(r.get("discount_pct", "0"))
        if pct <= 0 or pct > 100:
            continue
        risks.append({"kind": str(r.get("kind", "DISCOUNT"))[:50], "discount_pct": str(pct), "reason": str(r.get("reason", ""))[:500]})
    return risks


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
