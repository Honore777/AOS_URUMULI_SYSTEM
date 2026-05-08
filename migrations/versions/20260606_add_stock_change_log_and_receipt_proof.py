"""add stock change log and receipt proof

Revision ID: 20260606_stock_change_log
Revises: 20260605_merge_heads_unified_supplier
Create Date: 2026-06-06

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260606_stock_change_log'
down_revision = '240bb20aac69'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('customer_receipt', sa.Column('proof_image_path', sa.String(length=255), nullable=True))
    op.add_column('customer_receipt', sa.Column('proof_uploaded_at', sa.DateTime(), nullable=True))

    op.create_table(
        'stock_change_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mineral_type', sa.String(length=20), nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('before_json', sa.JSON(), nullable=True),
        sa.Column('after_json', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    op.create_index('ix_stock_change_log_mineral_type', 'stock_change_log', ['mineral_type'])
    op.create_index('ix_stock_change_log_stock_id', 'stock_change_log', ['stock_id'])
    op.create_index('ix_stock_change_log_action', 'stock_change_log', ['action'])
    op.create_index('ix_stock_change_log_created_at', 'stock_change_log', ['created_at'])


def downgrade():
    op.drop_index('ix_stock_change_log_created_at', table_name='stock_change_log')
    op.drop_index('ix_stock_change_log_action', table_name='stock_change_log')
    op.drop_index('ix_stock_change_log_stock_id', table_name='stock_change_log')
    op.drop_index('ix_stock_change_log_mineral_type', table_name='stock_change_log')
    op.drop_table('stock_change_log')
    op.drop_column('customer_receipt', 'proof_uploaded_at')
    op.drop_column('customer_receipt', 'proof_image_path')
