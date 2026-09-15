"""ARVO — Limits. Fonte verdade: PLANS em config.py. Princípio inegociável 2026-09-13."""
from sqlalchemy import func, select
from app.core.config import PLANS
from app.database.engine import get_sessionmaker
from app.database.models import Account, Finding, Membership

DEFAULT_PLAN = "starter"

def plan_for_org(extra_data: dict | None) -> str:
    if not extra_data: return DEFAULT_PLAN
    p = extra_data.get("plan", DEFAULT_PLAN)
    return p if p in PLANS else DEFAULT_PLAN

def plan_limits(plan: str) -> dict:
    return PLANS.get(plan, PLANS[DEFAULT_PLAN])

async def check_org_limits(org_id: str, kind: str) -> tuple[bool, str | None, dict]:
    """Check org against PLANS limits. kind: accounts|findings|users. Returns (ok, error, usage)."""
    async with get_sessionmaker()() as s:
        from app.database.models import Organization
        org = await s.get(Organization, org_id)
        if not org:
            return False, "org not found", {}
        plan = plan_for_org(org.extra_data)
        limits = plan_limits(plan)
        usage = {}
        # count current
        if kind == "accounts":
            cnt = (await s.execute(select(func.count(Account.id)).where(Account.org_id == org_id))).scalar() or 0
            max_v = limits["max_accounts"]
            usage = {"count": cnt, "max": max_v, "plan": plan}
            if cnt >= max_v:
                return False, f"Limite {plan}: {max_v} accounts. Upgrade necessário.", usage
        elif kind == "findings":
            cnt = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id))).scalar() or 0
            max_v = limits["max_findings"]
            usage = {"count": cnt, "max": max_v, "plan": plan}
            if cnt >= max_v:
                return False, f"Limite {plan}: {max_v} findings. Upgrade necessário.", usage
        elif kind == "users":
            cnt = (await s.execute(select(func.count(Membership.id)).where(Membership.org_id == org_id))).scalar() or 0
            max_v = limits["max_users"]
            usage = {"count": cnt, "max": max_v, "plan": plan}
            if cnt >= max_v:
                return False, f"Limite {plan}: {max_v} users. Upgrade necessário.", usage
        elif kind == "orgs":
            # not org-scoped — skip
            usage = {"plan": plan, "limits": limits}
        return True, None, usage

def human_limit_error(plan: str, kind: str) -> str:
    lim = plan_limits(plan)
    key = f"max_{kind}"
    return f"Plano {plan}: limite {lim.get(key, '?')} {kind}. Veja /pricing."
