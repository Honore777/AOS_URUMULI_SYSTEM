"""merge advance payment branch with main head

Revision ID: 20260424_merge_advance_branch
Revises: c3d7f5e9b2a1, 20260424_adv_fields
Create Date: 2026-04-24 12:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260424_merge_advance_branch'
down_revision = ('c3d7f5e9b2a1', '20260424_adv_fields')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
