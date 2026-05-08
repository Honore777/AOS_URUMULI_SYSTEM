"""merge heads

Revision ID: 240bb20aac69
Revises: 20260506_add_currency_to_cash_tables, 20260605_add_cash_reconciliation
Create Date: 2026-05-06 10:08:34.923374

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '240bb20aac69'
down_revision = ('20260506_add_currency_to_cash_tables', '20260605_add_cash_reconciliation')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
