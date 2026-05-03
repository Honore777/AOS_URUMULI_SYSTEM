"""Make stock_id nullable for advance payments.

Revision ID: 20260426_make_stock_id_nullable
Revises: 20260426_repair_advance_cols
Create Date: 2026-04-26 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260426_make_stock_id_nullable'
down_revision = '20260426_repair_advance_cols'
branch_labels = None
depends_on = None


def upgrade():
    # Make stock_id nullable for advance payments
    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table('cassiterite_supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table('cassiterite_supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=False)