"""Shared auth mutations (API + web). Callers own validation and response shape."""

from fastapi import HTTPException
from sqlalchemy import select

from app.core.limits import check_org_limits
from app.core.security import create_token, hash_password
from app.database.engine import get_sessionmaker
from app.database.models import ALL_ROLES, ROLE_ORDER, Membership, Organization, User


async def create_org_with_owner(s, email, password, org_name, org_slug, email_error, slug_error):
    if (await s.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(400, email_error)
    if (await s.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one_or_none():
        raise HTTPException(400, slug_error)
    org = Organization(name=org_name, slug=org_slug)
    s.add(org); await s.flush()
    user = User(email=email, hashed_password=hash_password(password))
    s.add(user); await s.flush()
    s.add(Membership(org_id=org.id, user_id=user.id, role="OWNER"))
    await s.commit()
    return create_token(user.id, org.id, "OWNER"), org.id, user.id


async def add_org_member(org_id, actor_role, email, role, higher_error):
    if actor_role not in ("OWNER", "ADMIN"):
        raise HTTPException(403, "Need OWNER/ADMIN")
    if role not in ALL_ROLES:
        raise HTTPException(400, f"Invalid role; use one of {ALL_ROLES}")
    if ROLE_ORDER[role] > ROLE_ORDER[actor_role]:
        raise HTTPException(403, higher_error)
    ok, err, _ = await check_org_limits(org_id, "users")
    if not ok:
        raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            raise HTTPException(404, "User not found; they must register first")
        if (await s.execute(select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id))).scalar_one_or_none():
            raise HTTPException(400, "User already in org")
        m = Membership(org_id=org_id, user_id=user.id, role=role)
        s.add(m); await s.commit(); await s.refresh(m)
        return m
