"""add cash transaction reference

Revision ID: 20260607_cash_tx_ref
Revises: 20260606_loans_unearned
Create Date: 2026-06-07

"""

from alembic import op
import sqlalchemy as sa


revision = '20260607_cash_tx_ref'
down_revision = '20260606_loans_unearned'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cash_transaction', sa.Column('reference', sa.String(length=140), nullable=True))
    op.create_index('ix_cash_transaction_reference', 'cash_transaction', ['reference'], unique=False)


def downgrade():
    op.drop_index('ix_cash_transaction_reference', table_name='cash_transaction')
    op.drop_column('cash_transaction', 'reference')
