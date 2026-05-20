"""Add indexes on output and payment tables to improve filter performance

Revision ID: 20260401_add_output_payment_indexes
Revises: 20260322_add_indexes_on_stock_tables
Create Date: 2026-04-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260401_add_output_payment_indexes'
down_revision = '20260322_add_indexes_on_stock_tables'
branch_labels = None
depends_on = None


def upgrade():
    # Copper output indexes
    # Copper output indexes (idempotent)
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_output_stock_id ON copper_output (stock_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_output_date ON copper_output (date)")

    # Copper supplier payment index (idempotent)
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_stock_id ON supplier_payment (stock_id)")

    # Cassiterite output indexes (idempotent)
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_output_stock_id ON cassiterite_output (stock_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_output_date ON cassiterite_output (date)")

    # Cassiterite supplier payment index (idempotent)
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_stock_id ON cassiterite_supplier_payment (stock_id)")


def downgrade():
    op.drop_index('ix_cassiterite_supplier_payment_stock_id', table_name='cassiterite_supplier_payment')
    op.drop_index('ix_cassiterite_output_date', table_name='cassiterite_output')
    op.drop_index('ix_cassiterite_output_stock_id', table_name='cassiterite_output')
    op.drop_index('ix_supplier_payment_stock_id', table_name='supplier_payment')
    op.drop_index('ix_copper_output_date', table_name='copper_output')
    op.drop_index('ix_copper_output_stock_id', table_name='copper_output')
