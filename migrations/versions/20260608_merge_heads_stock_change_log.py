"""merge heads for stock_change_log audit

Revision ID: 20260608_merge_heads_stock_change_log
Revises: 20260513_add_stock_change_log_audit, 20260607_add_push_token_table
Create Date: 2026-06-08

This is an empty merge migration that unifies the two divergent heads
so `flask db upgrade` can run to a single head.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260608_merge_heads_stock_change_log'
down_revision = ('20260513_add_stock_change_log_audit', '20260607_add_push_token_table')
branch_labels = None
depends_on = None


def upgrade():
    # merge-only migration; no DB operations required
    pass


def downgrade():
    pass
