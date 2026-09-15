"""ARVO — Security: JWT + bcrypt + capability guard."""

import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from app.core.config import settings

try:
    import bcrypt as _bcrypt
    def hash_password(p: str) -> str:
        return _bcrypt.hashpw(p.encode()[:72], _bcrypt.gensalt()).decode()
    def verify_password(p: str, h: str) -> bool:
        try:
            return _bcrypt.checkpw(p.encode()[:72], h.encode())
        except Exception:
            return False
except ImportError:
    from passlib.context import CryptContext
    _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_password(p: str) -> str: return _pwd.hash(p[:72])
    def verify_password(p, h) -> bool: return _pwd.verify(p[:72], h)

def create_token(sub: str, org_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "org_id": org_id, "role": role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def decode_token(tok: str) -> dict:
    try:
        return jwt.decode(tok, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def require_capability(user_role: str, cap: str):
    from app.database.models import has_capability
    if not has_capability(user_role, cap):
        raise HTTPException(status_code=403, detail=f"Forbidden: need {cap} (role {user_role})")
