"""ARVO — Accounts + Contacts API. Org-isolated, RLS via org_id filter."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import CONTACT_ROLES, Account, Contact
from app.api.deps import get_current_user, pagination
from app.api.ingest import parse_account_rows
from app.core.limits import check_org_limits

router = APIRouter(prefix="/accounts", tags=["accounts"])

ACCOUNT_TYPES = ("CUSTOMER", "PROSPECT", "PARTNER")


def _validate_csv_type(r):
    t = str(r.get("type", "CUSTOMER")).strip().upper()
    if t not in ACCOUNT_TYPES:
        raise HTTPException(400, f"type must be one of {ACCOUNT_TYPES}")

class AccountIn(BaseModel):
    name: str
    domain: str | None = None
    industry: str | None = None
    type: str = "CUSTOMER"

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v[:255]

    @field_validator("type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ACCOUNT_TYPES:
            raise ValueError(f"type must be one of {ACCOUNT_TYPES}")
        return v

class ContactIn(BaseModel):
    name: str
    email: str | None = None
    role: str = "OTHER"
    phone: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v[:255]

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email")
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in CONTACT_ROLES:
            raise ValueError(f"role must be one of {CONTACT_ROLES}")
        return v

@router.get("")
async def list_accounts(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Account)
            .where(Account.org_id==ctx["membership"].org_id)
            .order_by(Account.created_at.desc(), Account.id.desc())
            .offset(page["offset"]).limit(page["limit"])
        )).scalars().all()
        return [{"id":r.id,"name":r.name,"domain":r.domain,"type":r.type} for r in rows]

@router.post("")
async def create_account(inp: AccountIn, ctx=Depends(get_current_user)):
    ok, err, _ = await check_org_limits(ctx["membership"].org_id, "accounts")
    if not ok: raise HTTPException(402, err)
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
    valid = await parse_account_rows(rows, ctx["membership"].org_id, ("name", "account_name"), strip_filter=True, check_empty=True, validate=_validate_csv_type)
    async with get_sessionmaker()() as s:
        created=[]
        for r, name in valid:
            a=Account(org_id=ctx["membership"].org_id, name=name.strip()[:255], domain=(r.get("domain") or "").strip() or None, type=str(r.get("type","CUSTOMER")).upper(), owner_id=ctx["user"].id, extra_data={"import":"csv"})
            s.add(a); created.append(a)
        await s.commit()
        for a in created: await s.refresh(a)
        return {"created": len(created), "ids": [a.id for a in created]}
