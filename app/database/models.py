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

# ── Accounts ── Spec §152
class Account(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="CUSTOMER")  # CUSTOMER|PROSPECT|PARTNER
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    contacts = relationship("Contact", back_populates="account", cascade="all, delete-orphan")
    opportunities = relationship("Opportunity", back_populates="account", cascade="all, delete-orphan")

# ── Contacts ── roles ECONOMIC_BUYER, CHAMPION, etc
CONTACT_ROLES = ["ECONOMIC_BUYER","CHAMPION","DECISION_MAKER","TECHNICAL_EVALUATOR","PROCUREMENT","LEGAL","BLOCKER","INFLUENCER","OTHER"]
class Contact(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(50), default="OTHER")
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    account = relationship("Account", back_populates="contacts")

# ── Opportunities ──
OPP_STAGES = ["PROSPECTING","QUALIFICATION","PROPOSAL","NEGOTIATION","CLOSED_WON","CLOSED_LOST"]
class Opportunity(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(50), default="PROSPECTING")
    amount: Mapped[str] = mapped_column(String(50), default="0")  # Decimal as string for sqlite; use Numeric in postgres
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    close_date: Mapped[datetime | None] = mapped_column(nullable=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    account = relationship("Account", back_populates="opportunities")

# ── Conversations / Messages ── (canonical from ingest)
class Conversation(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    channel: Mapped[str] = mapped_column(String(50), default="MANUAL")  # EMAIL|MEETING|MANUAL|CRM
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(50), default="MANUAL")
    provenance: Mapped[dict] = mapped_column(SA_JSON, default=dict)  # source, raw_id, ingest
    conversation = relationship("Conversation", back_populates="messages")

# ── Ingest provenance stub ──
class RawRecord(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "raw_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(50))  # CSV|GMAIL|HUBSPOT|SALESFORCE|PIPEDRIVE|UPLOAD
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING|VALIDATED|NORMALIZED|REJECTED

# ── Commercial / Financial ── Spec §164-177 — Fase 4
# Products, Price lists, Proposals/Contracts, Invoices, Financial ledger, Findings

class Product(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    unit_price: Mapped[str] = mapped_column(String(50), default="0")  # Decimal string
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)

class Proposal(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")  # DRAFT|SENT|ACCEPTED|REJECTED
    total_amount: Mapped[str] = mapped_column(String(50), default="0")
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    discount_pct: Mapped[str] = mapped_column(String(10), default="0")
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    items = relationship("ProposalItem", back_populates="proposal", cascade="all, delete-orphan")

class ProposalItem(Base, TimestampMixin):
    __tablename__ = "proposal_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[str] = mapped_column(String(20), default="1")
    unit_price: Mapped[str] = mapped_column(String(50), default="0")
    discount_pct: Mapped[str] = mapped_column(String(10), default="0")
    proposal = relationship("Proposal", back_populates="items")

class Invoice(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True)
    number: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT|ISSUED|PAID|OVERDUE|CANCELLED
    total_amount: Mapped[str] = mapped_column(String(50), default="0")
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    due_date: Mapped[datetime | None] = mapped_column(nullable=True)
    extra_data: Mapped[dict] = mapped_column(SA_JSON, default=dict)

# Financial events / ledger — double-counting prevention via idempotency_key
class FinancialEvent(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "financial_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(50))  # REVENUE|DISCOUNT|RISK_EXPOSURE|RECOVERED
    amount: Mapped[str] = mapped_column(String(50), default="0")  # Decimal string, never float
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    idempotency_key: Mapped[str] = mapped_column(String(100), index=True)
    attribution_level: Mapped[int] = mapped_column(default=0)  # 0-4 spec
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provenance: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    __table_args__ = (Index("ix_financial_events_org_idem", "org_id", "idempotency_key", unique=True),)

class FinancialLedger(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "financial_ledger"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("financial_events.id", ondelete="SET NULL"), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(30))  # DEBIT|CREDIT|VERIFIED
    amount: Mapped[str] = mapped_column(String(50), default="0")
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

# Findings lifecycle — Spec §164 DETECTED→...→FINANCIALLY_VERIFIED
FINDING_STATUSES = ["DETECTED","TRIAGED","VALIDATING","VALIDATED","ACTION_PLANNED","ACTION_APPROVED","ACTION_EXECUTED","RESOLVED","FINANCIALLY_VERIFIED","REJECTED"]
class Finding(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(100), index=True)  # LEAKAGE|PRICING|DISCOUNT|COMMITMENT...
    status: Mapped[str] = mapped_column(String(30), default="DETECTED")
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")  # LOW|MEDIUM|HIGH|CRITICAL
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    exposure_amount: Mapped[str] = mapped_column(String(50), default="0")  # Decimal
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    confidence: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    evidence_pack: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    evidences = relationship("FindingEvidence", back_populates="finding", cascade="all, delete-orphan")

class FindingEvidence(Base, TimestampMixin):
    __tablename__ = "finding_evidences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # SOURCE|CALCULATION|EVIDENCE
    content: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    finding = relationship("Finding", back_populates="evidences")

# ── AI audit / extraction ──
class AiExtraction(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "ai_extractions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(50))  # DEAL|CONTRACT|EMAIL
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50), default="v1")
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[str] = mapped_column(String(20), default="0")
    latency_ms: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="OK")
    confidence: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    output: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(SA_JSON, default=dict)

class AiDecision(Base, TimestampMixin, OrgScopedMixin):
    __tablename__ = "ai_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(50))  # DEAL_AUDIT|MATERIALITY
    input_ref: Mapped[str] = mapped_column(String(100))
    output: Mapped[dict] = mapped_column(SA_JSON, default=dict)
    model: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[str] = mapped_column(String(20), default="MEDIUM")

ROLE_ORDER = {"OWNER":6, "ADMIN":5, "MANAGER":4, "ANALYST":3, "OPERATOR":2, "VIEWER":1}
ALL_ROLES = list(ROLE_ORDER.keys())

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
