"""Add receipt tracking for worker and supplier payments

Revision ID: 001_add_receipt_tracking
Revises: 
Create Date: 2026-05-17 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_add_receipt_tracking'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create worker_payment_receipt_sequence table
    op.create_table(
        'worker_payment_receipt_sequence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('next_sequence', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year')
    )

    # Create worker_payment_receipt table
    op.create_table(
        'worker_payment_receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('receipt_number', sa.String(50), nullable=False),
        sa.Column('worker_name', sa.String(120), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='RWF'),
        sa.Column('mineral_type', sa.String(20), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=True),
        sa.Column('generated_by_id', sa.Integer(), nullable=True),
        sa.Column('is_printed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('printed_at', sa.DateTime(), nullable=True),
        sa.Column('printed_by_id', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by_id', sa.Integer(), nullable=True),
        sa.Column('delete_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['payment_id'], ['expense_transaction.id']),
        sa.ForeignKeyConstraint(['generated_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['printed_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['deleted_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_number')
    )
    
    # Create indexes
    op.create_index('ix_worker_payment_receipt_payment_id', 'worker_payment_receipt', ['payment_id'])
    op.create_index('ix_worker_payment_receipt_receipt_number', 'worker_payment_receipt', ['receipt_number'])
    op.create_index('ix_worker_payment_receipt_worker_name', 'worker_payment_receipt', ['worker_name'])
    op.create_index('ix_worker_payment_receipt_mineral_type', 'worker_payment_receipt', ['mineral_type'])
    op.create_index('ix_worker_payment_receipt_generated_at', 'worker_payment_receipt', ['generated_at'])
    op.create_index('ix_worker_payment_receipt_is_printed', 'worker_payment_receipt', ['is_printed'])
    op.create_index('ix_worker_payment_receipt_is_deleted', 'worker_payment_receipt', ['is_deleted'])

    # Create supplier_payment_receipt_sequence table
    op.create_table(
        'supplier_payment_receipt_sequence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('next_sequence', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year')
    )

    # Create supplier_payment_receipt table
    op.create_table(
        'supplier_payment_receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('mineral_type', sa.String(20), nullable=False),
        sa.Column('receipt_number', sa.String(50), nullable=False),
        sa.Column('supplier_name', sa.String(120), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='RWF'),
        sa.Column('payment_type', sa.String(20), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=True),
        sa.Column('generated_by_id', sa.Integer(), nullable=True),
        sa.Column('is_printed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('printed_at', sa.DateTime(), nullable=True),
        sa.Column('printed_by_id', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by_id', sa.Integer(), nullable=True),
        sa.Column('delete_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['generated_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['printed_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['deleted_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_number')
    )
    
    # Create indexes
    op.create_index('ix_supplier_payment_receipt_payment_id', 'supplier_payment_receipt', ['payment_id'])
    op.create_index('ix_supplier_payment_receipt_mineral_type', 'supplier_payment_receipt', ['mineral_type'])
    op.create_index('ix_supplier_payment_receipt_receipt_number', 'supplier_payment_receipt', ['receipt_number'])
    op.create_index('ix_supplier_payment_receipt_supplier_name', 'supplier_payment_receipt', ['supplier_name'])
    op.create_index('ix_supplier_payment_receipt_generated_at', 'supplier_payment_receipt', ['generated_at'])
    op.create_index('ix_supplier_payment_receipt_is_printed', 'supplier_payment_receipt', ['is_printed'])
    op.create_index('ix_supplier_payment_receipt_is_deleted', 'supplier_payment_receipt', ['is_deleted'])


def downgrade():
    # Drop indexes for supplier_payment_receipt
    op.drop_index('ix_supplier_payment_receipt_is_deleted', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_is_printed', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_generated_at', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_supplier_name', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_receipt_number', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_mineral_type', 'supplier_payment_receipt')
    op.drop_index('ix_supplier_payment_receipt_payment_id', 'supplier_payment_receipt')
    
    # Drop tables
    op.drop_table('supplier_payment_receipt')
    op.drop_table('supplier_payment_receipt_sequence')
    
    # Drop indexes for worker_payment_receipt
    op.drop_index('ix_worker_payment_receipt_is_deleted', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_is_printed', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_generated_at', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_mineral_type', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_worker_name', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_receipt_number', 'worker_payment_receipt')
    op.drop_index('ix_worker_payment_receipt_payment_id', 'worker_payment_receipt')
    
    op.drop_table('worker_payment_receipt')
    op.drop_table('worker_payment_receipt_sequence')
