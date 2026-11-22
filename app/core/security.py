"""ARVO — Security: JWT + bcrypt + capability guard."""

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from app.core.config import settings


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode()[:72], h.encode())
    except Exception:
        return False


def normalize_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or "." not in v.split("@")[-1]:
        raise ValueError("invalid email")
    return v

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
