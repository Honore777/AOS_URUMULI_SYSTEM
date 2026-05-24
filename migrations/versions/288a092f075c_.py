"""empty message

Revision ID: 288a092f075c
Revises: 20260524_backfill_missing_supplier_slug, 9085969c27e8
Create Date: 2026-05-24 13:31:25.662684

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '288a092f075c'
down_revision = ('20260524_backfill_missing_supplier_slug', '9085969c27e8')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
