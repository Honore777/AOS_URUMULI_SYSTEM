"""add receipt handover fields

Revision ID: 20260607_receipt_handover
Revises: 20260607_cash_tx_ref
Create Date: 2026-06-07

"""

from alembic import op
import sqlalchemy as sa


revision = '20260607_receipt_handover'
down_revision = '20260607_cash_tx_ref'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('customer_receipt', sa.Column('is_handed_over', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('customer_receipt', sa.Column('handed_over_by_id', sa.Integer(), nullable=True))
    op.add_column('customer_receipt', sa.Column('handed_over_at', sa.DateTime(), nullable=True))
    op.create_index('ix_customer_receipt_is_handed_over', 'customer_receipt', ['is_handed_over'], unique=False)
    op.create_foreign_key('fk_customer_receipt_handed_over_by_id', 'customer_receipt', 'user', ['handed_over_by_id'], ['id'])

    op.add_column('customer_unearned_receipt', sa.Column('is_handed_over', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('customer_unearned_receipt', sa.Column('handed_over_by_id', sa.Integer(), nullable=True))
    op.add_column('customer_unearned_receipt', sa.Column('handed_over_at', sa.DateTime(), nullable=True))
    op.create_index('ix_customer_unearned_receipt_is_handed_over', 'customer_unearned_receipt', ['is_handed_over'], unique=False)
    op.create_foreign_key('fk_customer_unearned_receipt_handed_over_by_id', 'customer_unearned_receipt', 'user', ['handed_over_by_id'], ['id'])


def downgrade():
    op.drop_constraint('fk_customer_unearned_receipt_handed_over_by_id', 'customer_unearned_receipt', type_='foreignkey')
    op.drop_index('ix_customer_unearned_receipt_is_handed_over', table_name='customer_unearned_receipt')
    op.drop_column('customer_unearned_receipt', 'handed_over_at')
    op.drop_column('customer_unearned_receipt', 'handed_over_by_id')
    op.drop_column('customer_unearned_receipt', 'is_handed_over')

    op.drop_constraint('fk_customer_receipt_handed_over_by_id', 'customer_receipt', type_='foreignkey')
    op.drop_index('ix_customer_receipt_is_handed_over', table_name='customer_receipt')
    op.drop_column('customer_receipt', 'handed_over_at')
    op.drop_column('customer_receipt', 'handed_over_by_id')
    op.drop_column('customer_receipt', 'is_handed_over')
