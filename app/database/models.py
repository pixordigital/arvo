"""ARVO — Core models. Spec §152-160. All business objects org-scoped + RLS."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey, func, Index
from sqlalchemy import JSON as SA_JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.engine import Base

def _uuid(): return str(uuid.uuid4())
def _now(): return datetime.now(timezone.utc).replace(tzinfo=None)

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

class OrgScopedMixin:
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)

# ── Organization ──
class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    memberships = relationship("Membership", back_populates="organization", cascade="all, delete-orphan")
    users = relationship("User", secondary="memberships", viewonly=True)

# ── User ──
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")

# ── Membership (org + role) — capability-based per spec ──
# Roles: OWNER, ADMIN, MANAGER, ANALYST, OPERATOR, VIEWER
class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="VIEWER")  # OWNER|ADMIN|MANAGER|ANALYST|OPERATOR|VIEWER
    is_active: Mapped[bool] = mapped_column(default=True)
    organization = relationship("Organization", back_populates="memberships")
    user = relationship("User", back_populates="memberships")
    __table_args__ = (Index("ix_membership_org_user", "org_id", "user_id", unique=True),)

# Placeholder for future domain tables (accounts, contacts, etc) — Fase 3
# Keeping models minimal for Fase 2 auth/RLS; Alembic will add tables incrementally.

ROLE_ORDER = {"OWNER":6, "ADMIN":5, "MANAGER":4, "ANALYST":3, "OPERATOR":2, "VIEWER":1}
ALL_ROLES = list(ROLE_ORDER.keys())

# Capability map — prefer capability over role string in code
CAPABILITIES = {
    "org:manage": ["OWNER","ADMIN"],
    "org:invite": ["OWNER","ADMIN","MANAGER"],
    "findings:write": ["OWNER","ADMIN","MANAGER","ANALYST"],
    "findings:approve": ["OWNER","ADMIN","MANAGER"],
    "actions:execute": ["OWNER","ADMIN","MANAGER","OPERATOR"],
    "settings:manage": ["OWNER","ADMIN"],
    "data:export": ["OWNER","ADMIN","MANAGER","ANALYST"],
}

def has_capability(role: str, cap: str) -> bool:
    return role in CAPABILITIES.get(cap, [])
