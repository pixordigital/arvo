"""ARVO — FinancialCalculationService. Decimal, never float. Python calcula, LLM interpreta."""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Tuple

def D(v) -> Decimal:
    try: return Decimal(str(v))
    except InvalidOperation: return Decimal("0")

def money(v, places=2) -> str:
    q = Decimal("1." + "0"*places) if places else Decimal("1")
    # Actually quantize needs exponent: 1.00
    exp = Decimal("1." + "0"*places) if places else Decimal("1")
    # Use quantize with 0.01
    dec = D(v).quantize(Decimal("0.01") if places==2 else Decimal("1"), rounding=ROUND_HALF_UP)
    return format(dec, "f")

def calc_proposal_total(items: list[dict]) -> Tuple[str, str]:
    """items: [{quantity, unit_price, discount_pct}] → (gross, net) Decimal strings."""
    gross = Decimal("0")
    net = Decimal("0")
    for it in items:
        qty = D(it.get("quantity","1"))
        up = D(it.get("unit_price","0"))
        disc = D(it.get("discount_pct","0"))/Decimal("100")
        line_gross = qty * up
        line_net = line_gross * (Decimal("1")-disc)
        gross += line_gross
        net += line_net
    return money(gross), money(net)

def calc_discount_exposure(gross: str, net: str) -> str:
    return money(D(gross) - D(net))

def calc_ledger_balance(entries: list[dict]) -> str:
    bal = Decimal("0")
    for e in entries:
        amt = D(e.get("amount","0"))
        if e.get("entry_type")=="CREDIT": bal += amt
        else: bal -= amt
    return money(bal)

# Double-counting prevention: idempotency_key = f"{org_id}:{kind}:{source_id}"
def idempotency_key(org_id: str, kind: str, source_id: str) -> str:
    return f"{org_id}:{kind}:{source_id}"
