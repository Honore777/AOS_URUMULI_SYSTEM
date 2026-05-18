"""empty message

Revision ID: 4cbe49e8e0a6
Revises: 001_add_receipt_tracking, 20260608_merge_heads_stock_change_log
Create Date: 2026-05-17 12:01:26.269161

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4cbe49e8e0a6'
down_revision = ('001_add_receipt_tracking', '20260608_merge_heads_stock_change_log')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
