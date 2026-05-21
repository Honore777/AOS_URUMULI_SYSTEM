"""Add is_historical flag to unified_supplier_advance

Revision ID: 20260521_add_unified_supplier_advance_is_historical
Revises: 20260605_add_unified_supplier_advances
Create Date: 2026-05-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260521_add_unified_supplier_advance_is_historical'
down_revision = '20260605_add_unified_supplier_advances'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('unified_supplier_advance', sa.Column('is_historical', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_is_historical ON unified_supplier_advance (is_historical)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_is_historical")
    op.drop_column('unified_supplier_advance', 'is_historical')
