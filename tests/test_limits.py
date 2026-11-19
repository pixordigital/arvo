"""ARVO — Limits truth tests + pricing/home reflects PLANS."""
import pytest
from app.core.config import PLANS
from app.core.limits import plan_limits, plan_for_org

def test_plans_truth():
    assert "starter" in PLANS and "pro" in PLANS and "enterprise" in PLANS
    for p in PLANS.values():
        assert p["max_accounts"] > 0 and p["max_findings"] > 0

def test_plan_limits_lookup():
    assert plan_limits("starter")["max_accounts"] == 100
    assert plan_limits("pro")["max_findings"] == 500
    assert plan_limits("unknown") == plan_limits("starter")

def test_plan_for_org():
    assert plan_for_org(None) == "starter"
    assert plan_for_org({}) == "starter"
    assert plan_for_org({"plan": "pro"}) == "pro"
    assert plan_for_org({"plan": "bogus"}) == "starter"

@pytest.mark.asyncio
async def test_check_org_limits_within(tmp_path):
    # uses sqlite temp DB via monkeypatch settings.database_url
    from app.core.limits import check_org_limits
    from app.database.engine import get_sessionmaker, get_engine, Base
    from app.database.models import Organization
    # ensure clean DB for this test — use existing engine (arvo.db sqlite)
    async with get_sessionmaker()() as s:
        org = Organization(name="Test Limits", slug="test-limits-arvo")
        s.add(org); await s.commit(); await s.refresh(org)
        ok, err, usage = await check_org_limits(org.id, "accounts")
        assert ok and err is None
        assert usage["max"] == 100  # starter default
        # cleanup
        await s.delete(org); await s.commit()

async def test_bulk_import_cannot_exceed_account_limit():
    import uuid
    from fastapi.testclient import TestClient
    from app.core.security import create_token, hash_password
    from app.database.engine import get_sessionmaker
    from app.database.models import Account, Membership, Organization, User
    from app.main import app

    tag = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(name="Bulk", slug=f"bulk-{tag}")
        user = User(email=f"bulk-{tag}@arvo.test", hashed_password=hash_password("secret123"))
        s.add_all([org, user]); await s.flush()
        s.add(Membership(org_id=org.id, user_id=user.id, role="OWNER"))
        for i in range(100):
            s.add(Account(org_id=org.id, name=f"Existing {i}"))
        await s.commit()
        org_id, user_id = org.id, user.id

    c = TestClient(app, follow_redirects=False)
    h = {"Authorization": f"Bearer {create_token(user_id, org_id, 'OWNER')}"}
    r = c.post("/api/v1/accounts/import/csv", json={"rows": [{"name": "Overflow"}]}, headers=h)
    assert r.status_code == 402, r.text
    r = c.post("/api/v1/accounts", json={"name": "Overflow"}, headers=h)
    assert r.status_code == 402, r.text


async def test_member_limit_is_enforced():
    import uuid
    from fastapi.testclient import TestClient
    from app.core.security import create_token, hash_password
    from app.database.engine import get_sessionmaker
    from app.database.models import Membership, Organization, User
    from app.main import app

    tag = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(name="Users", slug=f"users-{tag}")
        owner = User(email=f"owner-{tag}@arvo.test", hashed_password=hash_password("secret123"))
        s.add_all([org, owner]); await s.flush()
        s.add(Membership(org_id=org.id, user_id=owner.id, role="OWNER"))
        for i in range(4):
            u = User(email=f"m{i}-{tag}@arvo.test", hashed_password=hash_password("secret123"))
            s.add(u); await s.flush()
            s.add(Membership(org_id=org.id, user_id=u.id, role="VIEWER"))
        extra = User(email=f"extra-{tag}@arvo.test", hashed_password=hash_password("secret123"))
        s.add(extra); await s.commit()
        org_id, owner_id = org.id, owner.id

    c = TestClient(app, follow_redirects=False)
    h = {"Authorization": f"Bearer {create_token(owner_id, org_id, 'OWNER')}"}
    r = c.post("/api/v1/auth/members", json={"email": f"extra-{tag}@arvo.test", "role": "VIEWER"}, headers=h)
    assert r.status_code == 402, r.text


def test_pricing_reflects_plans():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/pricing")
    assert r.status_code == 200
    for plan in ["starter", "pro", "enterprise"]:
        assert plan in r.text.lower()
        assert str(PLANS[plan]["max_accounts"]) in r.text

def test_home_has_truth_disclaimer():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/home")
    assert r.status_code == 200
    # principle verdade string
    assert "PLANS" in r.text or "limites" in r.text.lower()
