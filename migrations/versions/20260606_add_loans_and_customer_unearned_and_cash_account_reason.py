"""add loans and customer unearned revenue and cash account reason

Revision ID: 20260606_loans_unearned
Revises: 20260606_merge_stock_change_log
Create Date: 2026-06-06

"""

from alembic import op
import sqlalchemy as sa


revision = '20260606_loans_unearned'
down_revision = '20260606_merge_stock_change_log'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cash_account', sa.Column('create_reason', sa.Text(), nullable=True))

    op.create_table(
        'loan',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lender_name', sa.String(length=140), nullable=False),
        sa.Column('lender_name_norm', sa.String(length=160), nullable=False),
        sa.Column('principal_input', sa.Float(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF'),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('principal_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='PENDING_APPROVAL'),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('boss_approved_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('boss_approved_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('outstanding_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('disbursed_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('repaid_rwf', sa.Float(), nullable=False, server_default='0'),
    )
    op.create_index('ix_loan_lender_name', 'loan', ['lender_name'], unique=False)
    op.create_index('ix_loan_lender_name_norm', 'loan', ['lender_name_norm'], unique=False)
    op.create_index('ix_loan_currency', 'loan', ['currency'], unique=False)
    op.create_index('ix_loan_principal_rwf', 'loan', ['principal_rwf'], unique=False)
    op.create_index('ix_loan_status', 'loan', ['status'], unique=False)
    op.create_index('ix_loan_created_at', 'loan', ['created_at'], unique=False)
    op.create_index('ix_loan_outstanding_rwf', 'loan', ['outstanding_rwf'], unique=False)

    op.create_table(
        'loan_ledger_entry',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('loan_id', sa.Integer(), sa.ForeignKey('loan.id'), nullable=False),
        sa.Column('entry_type', sa.String(length=30), nullable=False),
        sa.Column('amount_input', sa.Float(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF'),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('amount_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('cash_account_id', sa.Integer(), sa.ForeignKey('cash_account.id'), nullable=True),
        sa.Column('cash_transaction_id', sa.Integer(), sa.ForeignKey('cash_transaction.id'), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
    )
    op.create_index('ix_loan_ledger_entry_loan_id', 'loan_ledger_entry', ['loan_id'], unique=False)
    op.create_index('ix_loan_ledger_entry_entry_type', 'loan_ledger_entry', ['entry_type'], unique=False)
    op.create_index('ix_loan_ledger_entry_currency', 'loan_ledger_entry', ['currency'], unique=False)
    op.create_index('ix_loan_ledger_entry_amount_rwf', 'loan_ledger_entry', ['amount_rwf'], unique=False)
    op.create_index('ix_loan_ledger_entry_cash_account_id', 'loan_ledger_entry', ['cash_account_id'], unique=False)
    op.create_index('ix_loan_ledger_entry_cash_transaction_id', 'loan_ledger_entry', ['cash_transaction_id'], unique=False)
    op.create_index('ix_loan_ledger_entry_created_at', 'loan_ledger_entry', ['created_at'], unique=False)

    op.create_table(
        'customer_unearned_receipt',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mineral_type', sa.String(length=20), nullable=True),
        sa.Column('customer', sa.String(length=100), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.Column('payment_channel', sa.String(length=20), nullable=False, server_default='CASH'),
        sa.Column('amount_input', sa.Float(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RWF'),
        sa.Column('exchange_rate', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('amount_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('remaining_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('proof_image_path', sa.String(length=255), nullable=True),
        sa.Column('proof_uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('is_collected', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('collected_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('collected_at', sa.DateTime(), nullable=True),
        sa.Column('cash_account_id', sa.Integer(), sa.ForeignKey('cash_account.id'), nullable=True),
    )
    op.create_index('ix_customer_unearned_receipt_customer', 'customer_unearned_receipt', ['customer'], unique=False)
    op.create_index('ix_customer_unearned_receipt_received_at', 'customer_unearned_receipt', ['received_at'], unique=False)
    op.create_index('ix_customer_unearned_receipt_payment_channel', 'customer_unearned_receipt', ['payment_channel'], unique=False)
    op.create_index('ix_customer_unearned_receipt_currency', 'customer_unearned_receipt', ['currency'], unique=False)
    op.create_index('ix_customer_unearned_receipt_amount_rwf', 'customer_unearned_receipt', ['amount_rwf'], unique=False)
    op.create_index('ix_customer_unearned_receipt_remaining_rwf', 'customer_unearned_receipt', ['remaining_rwf'], unique=False)
    op.create_index('ix_customer_unearned_receipt_is_collected', 'customer_unearned_receipt', ['is_collected'], unique=False)
    op.create_index('ix_customer_unearned_receipt_cash_account_id', 'customer_unearned_receipt', ['cash_account_id'], unique=False)

    op.create_table(
        'customer_unearned_allocation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('unearned_id', sa.Integer(), sa.ForeignKey('customer_unearned_receipt.id'), nullable=False),
        sa.Column('stock_mineral_type', sa.String(length=20), nullable=False),
        sa.Column('batch_id', sa.String(length=100), nullable=False),
        sa.Column('applied_amount_rwf', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
    )
    op.create_index('ix_customer_unearned_allocation_unearned_id', 'customer_unearned_allocation', ['unearned_id'], unique=False)
    op.create_index('ix_customer_unearned_allocation_stock_mineral_type', 'customer_unearned_allocation', ['stock_mineral_type'], unique=False)
    op.create_index('ix_customer_unearned_allocation_batch_id', 'customer_unearned_allocation', ['batch_id'], unique=False)
    op.create_index('ix_customer_unearned_allocation_created_at', 'customer_unearned_allocation', ['created_at'], unique=False)


def downgrade():
    op.drop_index('ix_customer_unearned_allocation_created_at', table_name='customer_unearned_allocation')
    op.drop_index('ix_customer_unearned_allocation_batch_id', table_name='customer_unearned_allocation')
    op.drop_index('ix_customer_unearned_allocation_stock_mineral_type', table_name='customer_unearned_allocation')
    op.drop_index('ix_customer_unearned_allocation_unearned_id', table_name='customer_unearned_allocation')
    op.drop_table('customer_unearned_allocation')

    op.drop_index('ix_customer_unearned_receipt_cash_account_id', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_is_collected', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_remaining_rwf', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_amount_rwf', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_currency', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_payment_channel', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_received_at', table_name='customer_unearned_receipt')
    op.drop_index('ix_customer_unearned_receipt_customer', table_name='customer_unearned_receipt')
    op.drop_table('customer_unearned_receipt')

    op.drop_index('ix_loan_ledger_entry_created_at', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_cash_transaction_id', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_cash_account_id', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_amount_rwf', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_currency', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_entry_type', table_name='loan_ledger_entry')
    op.drop_index('ix_loan_ledger_entry_loan_id', table_name='loan_ledger_entry')
    op.drop_table('loan_ledger_entry')

    op.drop_index('ix_loan_outstanding_rwf', table_name='loan')
    op.drop_index('ix_loan_created_at', table_name='loan')
    op.drop_index('ix_loan_status', table_name='loan')
    op.drop_index('ix_loan_principal_rwf', table_name='loan')
    op.drop_index('ix_loan_currency', table_name='loan')
    op.drop_index('ix_loan_lender_name_norm', table_name='loan')
    op.drop_index('ix_loan_lender_name', table_name='loan')
    op.drop_table('loan')

    op.drop_column('cash_account', 'create_reason')
