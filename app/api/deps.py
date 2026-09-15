"""ARVO — FastAPI deps: current_user + org + RLS injection."""

from fastapi import Request, Depends, HTTPException
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.core.security import decode_token

async def get_current_user(request: Request):
    # Try Authorization: Bearer <jwt> or cookie aios_token/arvo_token
    tok = None
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        tok = auth.split(" ",1)[1]
    else:
        tok = request.cookies.get("arvo_token") or request.cookies.get("aios_token")
    if not tok:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = decode_token(tok)
    # Fetch user + membership
    async with get_sessionmaker()() as s:
        from app.database.models import User, Membership
        user = await s.get(User, claims["sub"])
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User inactive")
        m = (await s.execute(select(Membership).where(Membership.user_id==user.id, Membership.org_id==claims["org_id"]))).scalar_one_or_none()
        if not m or not m.is_active:
            raise HTTPException(status_code=403, detail="No membership")
        request.state.user_id = user.id
        request.state.org_id = m.org_id
        request.state.role = m.role
        return {"user": user, "membership": m, "claims": claims}

def require_roles(*roles):
    async def _dep(ctx=Depends(get_current_user)):
        if ctx["membership"].role not in roles:
            raise HTTPException(status_code=403, detail=f"Need one of {roles}")
        return ctx
    return _dep
