"""Add soft-delete fields, request payload storage, and backfill booleans.

Revision ID: 20260426_soft_delete_and_request_payload
Revises: 20260424_merge_advance_branch
Create Date: 2026-04-26 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260426_soft_delete_and_request_payload'
down_revision = '20260424_merge_advance_branch'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment_review', schema=None) as batch_op:
        batch_op.add_column(sa.Column('request_payload', sa.Text(), nullable=True))

    for table_name in ('supplier_payment', 'worker_payment', 'cassiterite_supplier_payment', 'cassiterite_worker_payment'):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
            batch_op.add_column(sa.Column('deleted_by_id', sa.Integer(), nullable=True))

    for table_name in ('copper_stock', 'cassiterite_stock'):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
            batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))
            batch_op.add_column(sa.Column('deleted_by_id', sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column('delete_reason', sa.Text(), nullable=True))

    op.execute("UPDATE supplier_payment SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.execute("UPDATE worker_payment SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.execute("UPDATE cassiterite_supplier_payment SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.execute("UPDATE cassiterite_worker_payment SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.execute("UPDATE copper_stock SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.execute("UPDATE cassiterite_stock SET is_deleted = FALSE WHERE is_deleted IS NULL")


def downgrade():
    with op.batch_alter_table('cassiterite_stock', schema=None) as batch_op:
        batch_op.drop_column('delete_reason')
        batch_op.drop_column('deleted_by_id')
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('is_deleted')

    with op.batch_alter_table('copper_stock', schema=None) as batch_op:
        batch_op.drop_column('delete_reason')
        batch_op.drop_column('deleted_by_id')
        batch_op.drop_column('deleted_at')
        batch_op.drop_column('is_deleted')

    for table_name in ('cassiterite_worker_payment', 'cassiterite_supplier_payment', 'worker_payment', 'supplier_payment'):
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_column('deleted_by_id')
            batch_op.drop_column('deleted_at')
            batch_op.drop_column('is_deleted')

    with op.batch_alter_table('payment_review', schema=None) as batch_op:
        batch_op.drop_column('request_payload')
