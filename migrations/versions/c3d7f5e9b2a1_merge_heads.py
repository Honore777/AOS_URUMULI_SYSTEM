"""merge heads

Revision ID: c3d7f5e9b2a1
Revises: 20260401_add_output_payment_indexes, b1f9d8a0c3e4
Create Date: 2026-04-06 01:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d7f5e9b2a1'
down_revision = ('20260401_add_output_payment_indexes', 'b1f9d8a0c3e4')
branch_labels = None
depends_on = None


def upgrade():
    # Merge point: no schema changes required, this resolves multiple heads
    pass


def downgrade():
    # Downgrade would re-create the split heads; keep simple pass for safety.
    pass
