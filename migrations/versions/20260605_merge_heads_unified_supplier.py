"""Merge heads after unified supplier advances.

Revision ID: 20260605_merge_heads_unified_supplier
Revises: 20260503_merge_current_heads, 20260605_add_unified_supplier_advances
Create Date: 2026-06-05 00:00:00.000000
"""

from alembic import op


revision = '20260605_merge_heads_unified_supplier'
down_revision = (
    '20260503_merge_current_heads',
    '20260605_add_unified_supplier_advances',
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
