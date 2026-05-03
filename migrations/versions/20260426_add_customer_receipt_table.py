"""Add customer_receipt table for negotiator/accountant receipt events.

Revision ID: 20260426_add_customer_receipt_table
Revises: 20260426_make_stock_id_nullable
Create Date: 2026-04-26 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260426_add_customer_receipt_table'
down_revision = '20260426_make_stock_id_nullable'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mineral_type', sa.String(length=20), nullable=False),
        sa.Column('batch_id', sa.String(length=100), nullable=False),
        sa.Column('customer', sa.String(length=100), nullable=False),
        sa.Column('bulk_plan_id', sa.Integer(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('receipt_type', sa.String(length=30), nullable=False),
        sa.Column('payment_channel', sa.String(length=20), nullable=False),
        sa.Column('amount_input', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False),
        sa.Column('exchange_rate', sa.Float(), nullable=False),
        sa.Column('amount_rwf', sa.Float(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['bulk_plan_id'], ['bulk_output_plan.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index('ix_customer_receipt_mineral_type', 'customer_receipt', ['mineral_type'], unique=False)
    op.create_index('ix_customer_receipt_batch_id', 'customer_receipt', ['batch_id'], unique=False)
    op.create_index('ix_customer_receipt_customer', 'customer_receipt', ['customer'], unique=False)
    op.create_index('ix_customer_receipt_bulk_plan_id', 'customer_receipt', ['bulk_plan_id'], unique=False)
    op.create_index('ix_customer_receipt_received_at', 'customer_receipt', ['received_at'], unique=False)
    op.create_index('ix_customer_receipt_receipt_type', 'customer_receipt', ['receipt_type'], unique=False)
    op.create_index('ix_customer_receipt_payment_channel', 'customer_receipt', ['payment_channel'], unique=False)
    op.create_index('ix_customer_receipt_currency', 'customer_receipt', ['currency'], unique=False)


def downgrade():
    op.drop_index('ix_customer_receipt_currency', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_payment_channel', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_receipt_type', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_received_at', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_bulk_plan_id', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_customer', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_batch_id', table_name='customer_receipt')
    op.drop_index('ix_customer_receipt_mineral_type', table_name='customer_receipt')

    op.drop_table('customer_receipt')
