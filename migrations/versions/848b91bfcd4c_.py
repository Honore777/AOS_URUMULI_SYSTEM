"""empty message

Revision ID: 848b91bfcd4c
Revises: 20260609_add_weight_tracking_to_bulk_output_plan, f7b04679bfec
Create Date: 2026-06-09 17:19:30.646917

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '848b91bfcd4c'
down_revision = ('20260609_add_weight_tracking_to_bulk_output_plan', 'f7b04679bfec')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
