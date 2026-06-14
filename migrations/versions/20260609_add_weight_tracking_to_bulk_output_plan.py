"""add weight tracking fields to bulk_output_plan

Revision ID: 20260609_add_weight_tracking_to_bulk_output_plan
Revises: 20260615_add_batch_deduction_and_plan_currency
Create Date: 2026-06-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260609_add_weight_tracking_to_bulk_output_plan'
down_revision = '20260615_add_batch_deduction_and_plan_currency'
branch_labels = None
depends_on = None


def upgrade():
    # Add weight tracking fields to bulk_output_plan
    op.add_column('bulk_output_plan', sa.Column('gross_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('tare_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('net_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('moisture_percent', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('moisture_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('net_dry_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('sample_weight', sa.Float(), nullable=True))
    op.add_column('bulk_output_plan', sa.Column('final_weight', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('bulk_output_plan', 'final_weight')
    op.drop_column('bulk_output_plan', 'sample_weight')
    op.drop_column('bulk_output_plan', 'net_dry_weight')
    op.drop_column('bulk_output_plan', 'moisture_weight')
    op.drop_column('bulk_output_plan', 'moisture_percent')
    op.drop_column('bulk_output_plan', 'net_weight')
    op.drop_column('bulk_output_plan', 'tare_weight')
    op.drop_column('bulk_output_plan', 'gross_weight')
