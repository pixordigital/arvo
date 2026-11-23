"""deal audit jobs

Revision ID: c2d3e4f5a6b8
Revises: b1c2d3e4f5a7
Create Date: 2026-11-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c2d3e4f5a6b8'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('deal_audit_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('opportunity_id', sa.String(length=36), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_deal_audit_jobs_opportunity_id', 'deal_audit_jobs', ['opportunity_id'])
    op.create_index('ix_deal_audit_jobs_idempotency_key', 'deal_audit_jobs', ['idempotency_key'])
    op.create_index('ix_deal_audit_jobs_created_at', 'deal_audit_jobs', ['created_at'])
    op.create_index('ix_deal_audit_jobs_org_id', 'deal_audit_jobs', ['org_id'])
    op.create_index('ix_deal_audit_jobs_org_key', 'deal_audit_jobs', ['org_id', 'idempotency_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_deal_audit_jobs_org_key', table_name='deal_audit_jobs')
    op.drop_index('ix_deal_audit_jobs_org_id', table_name='deal_audit_jobs')
    op.drop_index('ix_deal_audit_jobs_created_at', table_name='deal_audit_jobs')
    op.drop_index('ix_deal_audit_jobs_idempotency_key', table_name='deal_audit_jobs')
    op.drop_index('ix_deal_audit_jobs_opportunity_id', table_name='deal_audit_jobs')
    op.drop_table('deal_audit_jobs')
