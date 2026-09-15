"""ARVO — Ingest: CSV + document upload + RAW→VALIDATION→CANONICAL."""

from fastapi import APIRouter, Depends, UploadFile, File
import csv, io, json
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import RawRecord, Account
from app.api.deps import get_current_user

router = APIRouter(prefix="/ingest", tags=["ingest"])

@router.post("/csv")
async def ingest_csv(file: UploadFile = File(...), ctx=Depends(get_current_user)):
    data = await file.read()
    text = data.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    async with get_sessionmaker()() as s:
        raw = RawRecord(org_id=ctx["membership"].org_id, source="CSV", source_id=file.filename, payload={"rows": rows, "filename": file.filename}, status="PENDING")
        s.add(raw)
        # Normalize → canonical (minimal): create accounts from rows with name/domain
        created=0
        for r in rows:
            name = r.get("name") or r.get("account_name") or r.get("company")
            if not name: continue
            a = Account(org_id=ctx["membership"].org_id, name=name.strip(), domain=r.get("domain"), type="CUSTOMER", owner_id=ctx["user"].id, extra_data={"ingest":"csv","raw":r})
            s.add(a); created+=1
        raw.status="NORMALIZED"
        await s.commit()
        return {"raw_id": raw.id, "rows": len(rows), "accounts_created": created}

@router.post("/upload")
async def ingest_upload(file: UploadFile = File(...), ctx=Depends(get_current_user)):
    data = await file.read()
    async with get_sessionmaker()() as s:
        raw = RawRecord(org_id=ctx["membership"].org_id, source="UPLOAD", source_id=file.filename, payload={"filename": file.filename, "size": len(data), "content_type": file.content_type}, status="PENDING")
        s.add(raw); await s.commit()
        return {"raw_id": raw.id, "filename": file.filename, "size": len(data)}

@router.get("/raw")
async def list_raw(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(RawRecord).where(RawRecord.org_id==ctx["membership"].org_id).order_by(RawRecord.created_at.desc()).limit(50))).scalars().all()
        return [{"id":r.id,"source":r.source,"status":r.status,"source_id":r.source_id} for r in rows]
