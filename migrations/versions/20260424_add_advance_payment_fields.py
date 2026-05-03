"""Add supplier advance fields and delete_reason metadata

Revision ID: 20260424_adv_fields
Revises: 6ce8f251b323
Create Date: 2026-04-24 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260424_adv_fields'
down_revision = '6ce8f251b323'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column('supplier_name', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('is_advance', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        batch_op.add_column(sa.Column('advance_remaining', sa.Float(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('delete_reason', sa.Text(), nullable=True))
        batch_op.create_index('ix_supplier_payment_supplier_name', ['supplier_name'], unique=False)
        batch_op.create_index('ix_supplier_payment_is_advance', ['is_advance'], unique=False)

    with op.batch_alter_table('cassiterite_supplier_payment', schema=None) as batch_op:
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column('supplier_name', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('is_advance', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        batch_op.add_column(sa.Column('advance_remaining', sa.Float(), nullable=False, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('delete_reason', sa.Text(), nullable=True))
        batch_op.create_index('ix_cassiterite_supplier_payment_supplier_name', ['supplier_name'], unique=False)
        batch_op.create_index('ix_cassiterite_supplier_payment_is_advance', ['is_advance'], unique=False)

    with op.batch_alter_table('worker_payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delete_reason', sa.Text(), nullable=True))

    with op.batch_alter_table('cassiterite_worker_payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delete_reason', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('cassiterite_worker_payment', schema=None) as batch_op:
        batch_op.drop_column('delete_reason')

    with op.batch_alter_table('worker_payment', schema=None) as batch_op:
        batch_op.drop_column('delete_reason')

    with op.batch_alter_table('cassiterite_supplier_payment', schema=None) as batch_op:
        batch_op.drop_index('ix_cassiterite_supplier_payment_is_advance')
        batch_op.drop_index('ix_cassiterite_supplier_payment_supplier_name')
        batch_op.drop_column('delete_reason')
        batch_op.drop_column('advance_remaining')
        batch_op.drop_column('is_advance')
        batch_op.drop_column('supplier_name')
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('supplier_payment', schema=None) as batch_op:
        batch_op.drop_index('ix_supplier_payment_is_advance')
        batch_op.drop_index('ix_supplier_payment_supplier_name')
        batch_op.drop_column('delete_reason')
        batch_op.drop_column('advance_remaining')
        batch_op.drop_column('is_advance')
        batch_op.drop_column('supplier_name')
        batch_op.alter_column('stock_id', existing_type=sa.Integer(), nullable=False)
