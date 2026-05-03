"""add total_expected_amount to bulk_output_plan

Revision ID: 214_add_total_expected_amount
Revises: 8ef07dead9e6
Create Date: 2026-04-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '214_add_total_expected_amount'
down_revision = '8ef07dead9e6'
branch_labels = None
depends_on = None


def upgrade():
    # Add total_expected_amount column to bulk_output_plan table
    op.add_column('bulk_output_plan', sa.Column('total_expected_amount', sa.Float(), nullable=True, server_default='0'))


def downgrade():
    # Remove the column if migration is rolled back
    op.drop_column('bulk_output_plan', 'total_expected_amount')
