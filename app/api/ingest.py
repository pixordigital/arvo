"""ARVO — Ingest: CSV + document upload + RAW→VALIDATION→CANONICAL."""

from fastapi import APIRouter, Depends, UploadFile, File
import csv, io, json
from sqlalchemy import select
from fastapi import HTTPException
from app.database.engine import get_sessionmaker
from app.database.models import RawRecord, Account
from app.api.deps import get_current_user, pagination
from app.core.limits import check_org_limits

router = APIRouter(prefix="/ingest", tags=["ingest"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

@router.post("/csv")
async def ingest_csv(file: UploadFile = File(...), ctx=Depends(get_current_user)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload exceeds 5 MB")
    text = data.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    valid = [r for r in rows if (r.get("name") or r.get("account_name") or r.get("company"))]
    ok, err, usage = await check_org_limits(ctx["membership"].org_id, "accounts", delta=len(valid))
    if not ok and valid: raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        raw = RawRecord(org_id=ctx["membership"].org_id, source="CSV", source_id=file.filename, payload={"rows": rows, "filename": file.filename}, status="PENDING")
        s.add(raw)
        # Normalize → canonical: create accounts from rows with a name
        created=0
        for r in valid:
            name = r.get("name") or r.get("account_name") or r.get("company")
            a = Account(org_id=ctx["membership"].org_id, name=name.strip()[:255], domain=(r.get("domain") or "").strip()[:255] or None, type="CUSTOMER", owner_id=ctx["user"].id, extra_data={"ingest":"csv","raw":r})
            s.add(a); created+=1
        raw.status="NORMALIZED"
        await s.commit()
        return {"raw_id": raw.id, "rows": len(rows), "accounts_created": created}

@router.post("/upload")
async def ingest_upload(file: UploadFile = File(...), ctx=Depends(get_current_user)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload exceeds 5 MB")
    async with get_sessionmaker()() as s:
        raw = RawRecord(org_id=ctx["membership"].org_id, source="UPLOAD", source_id=file.filename, payload={"filename": file.filename, "size": len(data), "content_type": file.content_type}, status="PENDING")
        s.add(raw); await s.commit()
        return {"raw_id": raw.id, "filename": file.filename, "size": len(data)}

@router.get("/raw")
async def list_raw(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(RawRecord)
            .where(RawRecord.org_id==ctx["membership"].org_id)
            .order_by(RawRecord.created_at.desc(), RawRecord.id.desc())
            .offset(page["offset"]).limit(page["limit"])
        )).scalars().all()
        return [{"id":r.id,"source":r.source,"status":r.status,"source_id":r.source_id} for r in rows]
