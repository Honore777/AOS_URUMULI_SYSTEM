"""Add copper advance allocation table.

Revision ID: 20260502_add_copper_advance_allocations
Revises: 20260426_repair_advance_cols
Create Date: 2026-05-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260502_add_copper_advance_allocations'
down_revision = '20260426_repair_advance_cols'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'copper_advance_allocation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('stock_id', sa.Integer(), sa.ForeignKey('copper_stock.id'), nullable=False),
        sa.Column('supplier_payment_id', sa.Integer(), sa.ForeignKey('supplier_payment.id'), nullable=False),
        sa.Column('applied_amount', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_copper_advance_allocation_stock_id', 'copper_advance_allocation', ['stock_id'], unique=False)
    op.create_index('ix_copper_advance_allocation_supplier_payment_id', 'copper_advance_allocation', ['supplier_payment_id'], unique=False)
    op.create_index('ix_copper_advance_allocation_created_at', 'copper_advance_allocation', ['created_at'], unique=False)


def downgrade():
    op.drop_index('ix_copper_advance_allocation_created_at', table_name='copper_advance_allocation')
    op.drop_index('ix_copper_advance_allocation_supplier_payment_id', table_name='copper_advance_allocation')
    op.drop_index('ix_copper_advance_allocation_stock_id', table_name='copper_advance_allocation')
    op.drop_table('copper_advance_allocation')