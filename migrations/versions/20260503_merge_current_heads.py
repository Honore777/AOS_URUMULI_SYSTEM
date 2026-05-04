"""Merge current Alembic heads.

Revision ID: 20260503_merge_current_heads
Revises: 20260429_add_cashier_and_receipt_collection, 20260603_add_cassiterite_advance_allocations, 214_add_total_expected_amount
Create Date: 2026-05-03 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '20260503_merge_current_heads'
down_revision = (
    '20260429_add_cashier_and_receipt_collection',
    '20260603_add_cassiterite_advance_allocations',
    '214_add_total_expected_amount',
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass