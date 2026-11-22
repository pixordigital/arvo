"""add perf indexes

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-11-22

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS keeps this safe on already-migrated Supabase.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_financial_ledger_event_id "
        "ON financial_ledger (event_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_integration_outbox_status_created_at "
        "ON integration_outbox (status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_findings_org_severity "
        "ON findings (org_id, severity)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_financial_ledger_org_entry_type "
        "ON financial_ledger (org_id, entry_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_financial_ledger_org_entry_type")
    op.execute("DROP INDEX IF EXISTS ix_findings_org_severity")
    op.execute("DROP INDEX IF EXISTS ix_integration_outbox_status_created_at")
    op.execute("DROP INDEX IF EXISTS ix_financial_ledger_event_id")
