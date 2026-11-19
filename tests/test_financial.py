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

def test_parse_ai_risks_validates_json():
    from app.services.deal_audit import parse_ai_risks
    assert parse_ai_risks('not json') == []
    out = parse_ai_risks('```json\n{"risks":[{"kind":"DISCOUNT","discount_pct":12.5,"reason":"x"},{"kind":"DISCOUNT","discount_pct":150},{"kind":"DISCOUNT","discount_pct":-2}]}\n```')
    assert out == [{"kind": "DISCOUNT", "discount_pct": "12.5", "reason": "x"}]


def test_llm_without_key_fails_loudly():
    import asyncio
    from app.agents.provider import OpenRouterProvider
    try:
        asyncio.run(OpenRouterProvider(api_key="", base_url="http://unused").generate("x"))
    except RuntimeError as e:
        assert "not configured" in str(e)
    else:
        raise AssertionError("missing API key must raise, not invent output")


async def test_verify_requires_evidence_and_caps_amount():
    import uuid
    from fastapi.testclient import TestClient
    from app.database.engine import get_sessionmaker
    from app.database.models import FindingEvidence
    from app.main import app

    c = TestClient(app, follow_redirects=False)
    slug = f"verify-{uuid.uuid4().hex[:8]}"
    reg = c.post("/api/v1/auth/register", json={
        "email": f"{slug}@arvo.test", "password": "secret123",
        "org_name": slug, "org_slug": slug})
    assert reg.status_code == 200
    tok = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    acc = c.post("/api/v1/accounts", json={"name": "Acme"}, headers=h).json()
    opp = c.post("/api/v1/opportunities", json={"account_id": acc["id"], "name": "Deal", "amount": "10000"}, headers=h).json()
    finding = c.post("/api/v1/findings", json={
        "kind": "LEAKAGE", "title": "Leak", "exposure_amount": "1000.00",
        "idempotency_key": f"{slug}-f1"}, headers=h).json()
    action = c.post("/api/v1/actions", json={"finding_id": finding["id"]}, headers=h)
    assert action.status_code == 200, action.text
    aid = action.json()["id"]
    assert c.post(f"/api/v1/actions/{aid}/approve", headers=h).status_code == 200
    assert c.post(f"/api/v1/actions/{aid}/execute", headers=h).status_code == 200

    no_ev = c.post(f"/api/v1/actions/{aid}/verify", json={"verified_amount": "500"}, headers=h)
    assert no_ev.status_code == 400 and "Evidence required" in no_ev.text

    async def add_evidence():
        async with get_sessionmaker()() as s:
            s.add(FindingEvidence(finding_id=finding["id"], kind="SOURCE", content={"proof": "contract"}))
            await s.commit()
    await add_evidence()

    over = c.post(f"/api/v1/actions/{aid}/verify", json={"verified_amount": "1000.01"}, headers=h)
    assert over.status_code == 400 and "exceeds exposure" in over.text
    ok = c.post(f"/api/v1/actions/{aid}/verify", json={"verified_amount": "500"}, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["verified_amount"] == "500.00"
    impact = c.get("/api/v1/financial-impact", headers=h).json()
    assert impact["verified"] == "500.00"


def test_brl_filter_formats_never_raises():
    from app.web.routes import _brl
    assert _brl("19500.00") == "R$ 19.500,00"
    assert _brl("12000.00") == "R$ 12.000,00"
    assert _brl(None) == "—"
    assert _brl("garbage") == "—"
