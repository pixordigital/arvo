"""ARVO — Auth endpoints: register, login, me."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import User, Membership
from app.core.security import verify_password, create_token, normalize_email
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
        return normalize_email(v)

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
    from app.services.auth_service import create_org_with_owner
    async with get_sessionmaker()() as s:
        tok, org_id, user_id = await create_org_with_owner(
            s, inp.email, inp.password, inp.org_name, inp.org_slug, "Email exists", "Slug exists"
        )
    return {"access_token": tok, "token_type": "bearer", "org_id": org_id, "user_id": user_id}

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
        users = {u.id: u.email for u in (await s.execute(select(User).where(User.id.in_([m.user_id for m in rows])))).scalars().all()}
        return [{"user_id": m.user_id, "email": users.get(m.user_id), "role": m.role, "active": m.is_active} for m in rows]


@router.post("/members")
async def add_member(inp: MemberIn, ctx=Depends(get_current_user)):
    from app.services.auth_service import add_org_member
    m = await add_org_member(
        ctx["membership"].org_id, ctx["membership"].role, inp.email, inp.role,
        "Cannot assign higher role",
    )
    return {"id": m.id, "user_id": m.user_id, "role": m.role}
