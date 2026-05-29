"""add transporter ledger links and entry type

Revision ID: 20260620_add_transporter_ledger_links_and_entry_type
Revises: 20260620_add_supplier_deduction_and_transporter_ledger
Create Date: 2026-06-20 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260620_add_transporter_ledger_links_and_entry_type'
down_revision = '20260620_add_supplier_deduction_and_transporter_ledger'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('transporter_ledger', sa.Column('supplier_name', sa.String(length=140), nullable=True))
    op.add_column('transporter_ledger', sa.Column('entry_type', sa.String(length=40), nullable=False, server_default='TRANSPORT_FEE'))
    op.add_column('transporter_ledger', sa.Column('source_supplier_deduction_id', sa.Integer(), nullable=True))
    op.add_column('transporter_ledger', sa.Column('payment_review_id', sa.Integer(), nullable=True))
    op.add_column('transporter_ledger', sa.Column('cash_transaction_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_transporter_ledger_supplier_name'), 'transporter_ledger', ['supplier_name'], unique=False)
    op.create_index(op.f('ix_transporter_ledger_entry_type'), 'transporter_ledger', ['entry_type'], unique=False)
    op.create_index(op.f('ix_transporter_ledger_source_supplier_deduction_id'), 'transporter_ledger', ['source_supplier_deduction_id'], unique=False)
    op.create_index(op.f('ix_transporter_ledger_payment_review_id'), 'transporter_ledger', ['payment_review_id'], unique=False)
    op.create_index(op.f('ix_transporter_ledger_cash_transaction_id'), 'transporter_ledger', ['cash_transaction_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_transporter_ledger_cash_transaction_id'), table_name='transporter_ledger')
    op.drop_index(op.f('ix_transporter_ledger_payment_review_id'), table_name='transporter_ledger')
    op.drop_index(op.f('ix_transporter_ledger_source_supplier_deduction_id'), table_name='transporter_ledger')
    op.drop_index(op.f('ix_transporter_ledger_entry_type'), table_name='transporter_ledger')
    op.drop_index(op.f('ix_transporter_ledger_supplier_name'), table_name='transporter_ledger')
    op.drop_column('transporter_ledger', 'cash_transaction_id')
    op.drop_column('transporter_ledger', 'payment_review_id')
    op.drop_column('transporter_ledger', 'source_supplier_deduction_id')
    op.drop_column('transporter_ledger', 'entry_type')
    op.drop_column('transporter_ledger', 'supplier_name')
