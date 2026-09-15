"""ARVO — Accounts + Contacts API. Org-isolated, RLS via org_id filter."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Account, Contact
from app.api.deps import get_current_user

router = APIRouter(prefix="/accounts", tags=["accounts"])

class AccountIn(BaseModel):
    name: str
    domain: str | None = None
    industry: str | None = None
    type: str = "CUSTOMER"

class ContactIn(BaseModel):
    name: str
    email: str | None = None
    role: str = "OTHER"
    phone: str | None = None

@router.get("")
async def list_accounts(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(Account).where(Account.org_id==ctx["membership"].org_id).order_by(Account.created_at.desc()))).scalars().all()
        return [{"id":r.id,"name":r.name,"domain":r.domain,"type":r.type} for r in rows]

@router.post("")
async def create_account(inp: AccountIn, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        a = Account(org_id=ctx["membership"].org_id, name=inp.name, domain=inp.domain, industry=inp.industry, type=inp.type, owner_id=ctx["user"].id)
        s.add(a); await s.commit(); await s.refresh(a)
        return {"id": a.id, "name": a.name}

@router.get("/{aid}")
async def get_account(aid: str, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        a = await s.get(Account, aid)
        if not a or a.org_id != ctx["membership"].org_id: raise HTTPException(404)
        contacts = (await s.execute(select(Contact).where(Contact.account_id==aid))).scalars().all()
        return {"id":a.id,"name":a.name,"domain":a.domain,"contacts":[{"id":c.id,"name":c.name,"email":c.email,"role":c.role} for c in contacts]}

@router.post("/{aid}/contacts")
async def add_contact(aid: str, inp: ContactIn, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        a = await s.get(Account, aid)
        if not a or a.org_id != ctx["membership"].org_id: raise HTTPException(404)
        c = Contact(org_id=ctx["membership"].org_id, account_id=aid, name=inp.name, email=inp.email, role=inp.role, phone=inp.phone)
        s.add(c); await s.commit(); await s.refresh(c)
        return {"id": c.id}

@router.post("/import/csv")
async def import_csv(data: dict, ctx=Depends(get_current_user)):
    """Bulk import — MVP CSV/Upload endpoint. Expects {"rows":[{"name","domain"}]}."""
    rows = data.get("rows", [])
    async with get_sessionmaker()() as s:
        created=[]
        for r in rows:
            a=Account(org_id=ctx["membership"].org_id, name=r.get("name") or r.get("account_name") or "Unnamed", domain=r.get("domain"), type=r.get("type","CUSTOMER"), owner_id=ctx["user"].id, extra_data={"import":"csv"})
            s.add(a); created.append(a)
        await s.commit()
        for a in created: await s.refresh(a)
        return {"created": len(created), "ids": [a.id for a in created]}
