"""add supplier_deduction and transporter_ledger tables

Revision ID: 20260620_add_supplier_deduction_and_transporter_ledger
Revises: 7610605b5bb7
Create Date: 2026-06-20 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260620_add_supplier_deduction_and_transporter_ledger'
down_revision = '7610605b5bb7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_deduction',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('supplier_name', sa.String(length=140), nullable=False, index=True),
        sa.Column('deduction_type', sa.String(length=30), nullable=False, index=True),
        sa.Column('amount_input', sa.Numeric(18, 2), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF', index=True),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('amount_rwf', sa.Numeric(18, 2), nullable=False, server_default='0.0', index=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'), index=True),
        sa.Column('note', sa.Text(), nullable=True),
    )

    op.create_table(
        'transporter_ledger',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('transporter_name', sa.String(length=140), nullable=False, index=True),
        sa.Column('amount_input', sa.Numeric(18, 2), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF', index=True),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('amount_rwf', sa.Numeric(18, 2), nullable=False, server_default='0.0', index=True),
        sa.Column('is_paid', sa.Boolean(), nullable=False, server_default='false', index=True),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'), index=True),
        sa.Column('note', sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table('transporter_ledger')
    op.drop_table('supplier_deduction')
