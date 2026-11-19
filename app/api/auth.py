"""ARVO — Auth endpoints: register, login, me."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import User, Organization, Membership
from app.core.security import hash_password, verify_password, create_token
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterIn(BaseModel):
    email: str
    password: str
    org_name: str
    org_slug: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email")
        return v

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must have at least 8 characters")
        return v

    @field_validator("org_slug")
    @classmethod
    def valid_slug(cls, v: str) -> str:
        import re
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9-]{2,100}", v):
            raise ValueError("slug must be lowercase letters, digits or hyphen")
        return v

class LoginIn(BaseModel):
    email: str
    password: str

@router.post("/register")
async def register(inp: RegisterIn):
    async with get_sessionmaker()() as s:
        if (await s.execute(select(User).where(User.email==inp.email))).scalar_one_or_none():
            raise HTTPException(400, "Email exists")
        if (await s.execute(select(Organization).where(Organization.slug==inp.org_slug))).scalar_one_or_none():
            raise HTTPException(400, "Slug exists")
        org = Organization(name=inp.org_name, slug=inp.org_slug)
        s.add(org); await s.flush()
        user = User(email=inp.email, hashed_password=hash_password(inp.password))
        s.add(user); await s.flush()
        mem = Membership(org_id=org.id, user_id=user.id, role="OWNER")
        s.add(mem); await s.commit()
        tok = create_token(user.id, org.id, "OWNER")
        return {"access_token": tok, "token_type":"bearer", "org_id": org.id, "user_id": user.id}

@router.post("/login")
async def login(inp: LoginIn):
    async with get_sessionmaker()() as s:
        user = (await s.execute(select(User).where(User.email==inp.email))).scalar_one_or_none()
        if not user or not verify_password(inp.password, user.hashed_password):
            raise HTTPException(401, "Invalid credentials")
        mem = (await s.execute(select(Membership).where(Membership.user_id==user.id))).scalars().first()
        if not mem: raise HTTPException(403, "No org")
        tok = create_token(user.id, mem.org_id, mem.role)
        return {"access_token": tok, "token_type":"bearer", "org_id": mem.org_id, "role": mem.role}

@router.get("/me")
async def me(ctx=Depends(get_current_user)):
    return {"user_id": ctx["user"].id, "org_id": ctx["membership"].org_id, "role": ctx["membership"].role, "email": ctx["user"].email}


class MemberIn(BaseModel):
    email: str
    role: str = "VIEWER"


@router.get("/members")
async def list_members(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(Membership).where(Membership.org_id==ctx["membership"].org_id))).scalars().all()
        out = []
        for m in rows:
            u = await s.get(User, m.user_id)
            out.append({"user_id": m.user_id, "email": u.email if u else None, "role": m.role, "active": m.is_active})
        return out


@router.post("/members")
async def add_member(inp: MemberIn, ctx=Depends(get_current_user)):
    if ctx["membership"].role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Need OWNER/ADMIN")
    from app.core.limits import check_org_limits
    from app.database.models import ALL_ROLES
    if inp.role not in ALL_ROLES:
        raise HTTPException(400, f"Invalid role; use one of {ALL_ROLES}")
    ok, err, _ = await check_org_limits(ctx["membership"].org_id, "users")
    if not ok: raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        user = (await s.execute(select(User).where(User.email==inp.email))).scalar_one_or_none()
        if not user: raise HTTPException(404, "User not found; they must register first")
        existing = (await s.execute(select(Membership).where(Membership.user_id==user.id, Membership.org_id==ctx["membership"].org_id))).scalar_one_or_none()
        if existing: raise HTTPException(400, "User already in org")
        m = Membership(org_id=ctx["membership"].org_id, user_id=user.id, role=inp.role)
        s.add(m); await s.commit(); await s.refresh(m)
        return {"id": m.id, "user_id": user.id, "role": m.role}
