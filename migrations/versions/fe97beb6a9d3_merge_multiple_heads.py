"""merge multiple heads

Revision ID: fe97beb6a9d3
Revises: 20260517_convert_money_to_numeric, 4cbe49e8e0a6, 20260615_add_batch_deduction_and_plan_currency
Create Date: 2026-05-17 22:49:30.638773

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fe97beb6a9d3'
down_revision = ('20260517_convert_money_to_numeric', '4cbe49e8e0a6', '20260615_add_batch_deduction_and_plan_currency')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
