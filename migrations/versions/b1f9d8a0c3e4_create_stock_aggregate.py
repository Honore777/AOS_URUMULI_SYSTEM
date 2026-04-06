"""create stock_aggregate

Revision ID: b1f9d8a0c3e4
Revises: 6ce8f251b323
Create Date: 2026-04-06 01:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1f9d8a0c3e4'
down_revision = '6ce8f251b323'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_aggregate',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mineral_type', sa.String(length=32), nullable=False, unique=True),
        sa.Column('total_quantity', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_weighted_percent', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_t_unity', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('stock_aggregate')
