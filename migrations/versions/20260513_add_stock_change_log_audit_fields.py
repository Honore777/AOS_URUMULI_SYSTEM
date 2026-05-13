"""add audit columns to stock_change_log

Revision ID: 20260513_add_stock_change_log_audit
Revises: 20260606_stock_change_log
Create Date: 2026-05-13

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260513_add_stock_change_log_audit'
down_revision = '20260606_stock_change_log'
branch_labels = None
depends_on = None


def upgrade():
    # Add audit columns for reason editing
    op.add_column('stock_change_log', sa.Column('original_reason', sa.Text(), nullable=True))
    op.add_column('stock_change_log', sa.Column('reason_edited_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True))
    op.add_column('stock_change_log', sa.Column('reason_edited_at', sa.DateTime(), nullable=True))
    op.add_column('stock_change_log', sa.Column('reason_edit_reason', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('stock_change_log', 'reason_edit_reason')
    op.drop_column('stock_change_log', 'reason_edited_at')
    op.drop_column('stock_change_log', 'reason_edited_by_id')
    op.drop_column('stock_change_log', 'original_reason')
