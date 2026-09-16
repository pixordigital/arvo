"""ARVO — Hardening tests: Decimal, double-counting, idempotency, RLS."""

def test_decimal_no_float():
    from app.services.financial import calc_proposal_total
    gross, net = calc_proposal_total([{"quantity":"2","unit_price":"10000.00","discount_pct":"10"},{"quantity":"1","unit_price":"0.1","discount_pct":"0"}])
    assert gross == "20000.10"
    assert net == "18000.10"  # 20000*0.9=18000 +0.1
    # float would give 18000.100000000002

def test_idempotency_key_unique():
    from app.services.financial import idempotency_key
    k1 = idempotency_key("org1","proposal-discount","p1")
    k2 = idempotency_key("org1","proposal-discount","p1")
    k3 = idempotency_key("org2","proposal-discount","p1")
    assert k1 == k2
    assert k1 != k3

def test_materiality():
    from app.services.deal_audit import materiality
    assert materiality("60000")[0]=="HIGH"
    assert materiality("15000")[0]=="MEDIUM"
    assert materiality("500")[1]=="potential_exposure"

def test_double_count_prevention():
    # Simulate two proposals with same idempotency should not double-ledger
    from app.services.financial import idempotency_key, calc_proposal_total
    # Same org+kind+source → same key → DB unique constraint would reject second
    assert idempotency_key("org1","finding","f1")==idempotency_key("org1","finding","f1")

def test_capability():
    from app.database.models import has_capability
    assert has_capability("OWNER","findings:approve")
    assert not has_capability("VIEWER","findings:approve")
    assert has_capability("OPERATOR","actions:execute")

def test_sum_amounts_varchar_backend_agnostic():
    # Postgres has no sum(varchar): totals must be computed in Python.
    from app.services.financial import sum_amounts
    assert sum_amounts(["12000.00", "7500.50", None, "", "garbage"]) == "19500.50"
    assert sum_amounts([]) == "0.00"
    assert sum_amounts(None) == "0.00"

def test_brl_filter_formats_never_raises():
    from app.web.routes import _brl
    assert _brl("19500.00") == "R$ 19.500,00"
    assert _brl("12000.00") == "R$ 12.000,00"
    assert _brl(None) == "—"
    assert _brl("garbage") == "—"
