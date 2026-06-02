"""add soft delete columns to copper and cassiterite outputs

Revision ID: 20260625_add_soft_delete_to_copper_cassiterite_outputs
Revises: 20260620_add_transporter_ledger_links_and_entry_type
Create Date: 2026-06-25 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260625_add_soft_delete_to_copper_cassiterite_outputs'
down_revision = '20260620_add_transporter_ledger_links_and_entry_type'
branch_labels = None
depends_on = None


def upgrade():
    # Add soft delete columns to copper_output
    op.add_column('copper_output', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('copper_output', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('copper_output', sa.Column('deleted_by_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_copper_output_is_deleted'), 'copper_output', ['is_deleted'], unique=False)
    
    # Add soft delete columns to cassiterite_output
    op.add_column('cassiterite_output', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('cassiterite_output', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('cassiterite_output', sa.Column('deleted_by_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_cassiterite_output_is_deleted'), 'cassiterite_output', ['is_deleted'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_cassiterite_output_is_deleted'), table_name='cassiterite_output')
    op.drop_column('cassiterite_output', 'deleted_by_id')
    op.drop_column('cassiterite_output', 'deleted_at')
    op.drop_column('cassiterite_output', 'is_deleted')
    
    op.drop_index(op.f('ix_copper_output_is_deleted'), table_name='copper_output')
    op.drop_column('copper_output', 'deleted_by_id')
    op.drop_column('copper_output', 'deleted_at')
    op.drop_column('copper_output', 'is_deleted')
