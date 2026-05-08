"""add push token table

Revision ID: 20260607_add_push_token_table
Revises: 20260607_receipt_handover
Create Date: 2026-05-07

"""

from alembic import op
import sqlalchemy as sa


revision = '20260607_add_push_token_table'
down_revision = '20260607_receipt_handover'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'push_token',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('token', sa.Text(), nullable=False, unique=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_push_token_user_id', 'push_token', ['user_id'], unique=False)


def downgrade():
    op.drop_index('ix_push_token_user_id', table_name='push_token')
    op.drop_table('push_token')
