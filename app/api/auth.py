"""ARVO — Auth endpoints: register, login, me."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
