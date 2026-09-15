"""ARVO — Integrations API: list CRM adapters + sync + evidence pack."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import RawRecord, Account, Contact
from app.integrations.crm.base import ADAPTERS
from app.services.evidence import build_pack
from app.api.deps import get_current_user

router = APIRouter(prefix="/integrations", tags=["integrations"])

@router.get("")
async def list_integrations(ctx=Depends(get_current_user)):
    return [{"source": k, "adapter": v.source, "status":"stub"} for k,v in ADAPTERS.items()] + [{"source":"csv","status":"ready"},{"source":"upload","status":"ready"}]

@router.post("/sync/{source}")
async def sync_source(source: str, ctx=Depends(get_current_user)):
    ad = ADAPTERS.get(source.lower())
    if not ad: raise HTTPException(404, f"Unknown {source}")
    raw_list = await ad.fetch({"org_id": ctx["membership"].org_id})
    async with get_sessionmaker()() as s:
        created=0
        for raw in raw_list:
            rr=RawRecord(org_id=ctx["membership"].org_id, source=ad.source, source_id=str(raw.get("id") or raw.get("Id")), payload=raw, status="VALIDATED")
            s.add(rr)
            name = raw.get("company") or raw.get("Name") or raw.get("name") or raw.get("org_name") or "Imported"
            a=Account(org_id=ctx["membership"].org_id, name=str(name), domain=raw.get("domain") or raw.get("Website"), type="CUSTOMER", owner_id=ctx["user"].id, extra_data={"source": ad.source, "raw": raw})
            s.add(a); await s.flush()
            if raw.get("contact") or raw.get("email"):
                c=Contact(org_id=ctx["membership"].org_id, account_id=a.id, name=raw.get("contact","Contact"), email=raw.get("email"), role="OTHER")
                s.add(c)
            created+=1
        await s.commit()
        return {"source": ad.source, "fetched": len(raw_list), "accounts_created": created}

@router.get("/evidence/{finding_id}")
async def get_evidence(finding_id: str, ctx=Depends(get_current_user)):
    pack = await build_pack(finding_id, ctx["membership"].org_id)
    if not pack: raise HTTPException(404)
    return pack
