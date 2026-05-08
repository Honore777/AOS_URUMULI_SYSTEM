"""merge heads for stock change log

Revision ID: 20260606_merge_stock_change_log
Revises: 20260606_stock_change_log, 240bb20aac69
Create Date: 2026-06-06

"""

from alembic import op


revision = '20260606_merge_stock_change_log'
down_revision = ('20260606_stock_change_log', '240bb20aac69')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
