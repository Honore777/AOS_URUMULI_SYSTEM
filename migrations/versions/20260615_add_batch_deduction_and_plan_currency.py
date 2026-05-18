"""add batch_deduction table and plan currency/exchange_rate

Revision ID: 20260615_add_batch_deduction_and_plan_currency
Revises: 
Create Date: 2026-06-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260615_add_batch_deduction_and_plan_currency'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add currency and exchange_rate to bulk_output_plan
    op.add_column('bulk_output_plan', sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF'))
    op.add_column('bulk_output_plan', sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'))

    # Create batch_deduction table
    op.create_table(
        'batch_deduction',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('batch_id', sa.Integer, sa.ForeignKey('bulk_output_plan.id'), nullable=False, index=True),
        sa.Column('deduction_type', sa.String(length=50), nullable=False),
        sa.Column('amount_input', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF'),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('amount_rwf', sa.Numeric(18, 2), nullable=False, server_default='0'),
        sa.Column('created_by_id', sa.Integer, sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, index=True),
        sa.Column('note', sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table('batch_deduction')
    op.drop_column('bulk_output_plan', 'exchange_rate')
    op.drop_column('bulk_output_plan', 'currency')
