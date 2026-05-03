"""Add normalized currency columns to output tables.

Revision ID: 20260426_add_output_rwf_columns
Revises: 20260426_add_customer_receipt_table
Create Date: 2026-04-26 23:45:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260426_add_output_rwf_columns"
down_revision = "20260426_add_customer_receipt_table"
branch_labels = None
depends_on = None


def upgrade():
    # Copper output normalization fields
    op.execute("ALTER TABLE copper_output ADD COLUMN IF NOT EXISTS output_amount_rwf DOUBLE PRECISION")
    op.execute("ALTER TABLE copper_output ADD COLUMN IF NOT EXISTS amount_paid_rwf DOUBLE PRECISION")
    op.execute("ALTER TABLE copper_output ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'RWF'")
    op.execute("ALTER TABLE copper_output ADD COLUMN IF NOT EXISTS exchange_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE copper_output ADD COLUMN IF NOT EXISTS payment_stage VARCHAR(30) NOT NULL DEFAULT 'FULL_SETTLEMENT'")

    # Backfill normalized amounts for historical rows
    op.execute("UPDATE copper_output SET output_amount_rwf = COALESCE(output_amount_rwf, output_amount, 0)")
    op.execute("UPDATE copper_output SET amount_paid_rwf = COALESCE(amount_paid_rwf, amount_paid, 0)")

    # Performance indexes for query paths used by ledgers/dashboard
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_output_currency ON copper_output (currency)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_output_payment_stage ON copper_output (payment_stage)")

    # Cassiterite output normalization fields
    op.execute("ALTER TABLE cassiterite_output ADD COLUMN IF NOT EXISTS output_amount_rwf DOUBLE PRECISION")
    op.execute("ALTER TABLE cassiterite_output ADD COLUMN IF NOT EXISTS amount_paid_rwf DOUBLE PRECISION")
    op.execute("ALTER TABLE cassiterite_output ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'RWF'")
    op.execute("ALTER TABLE cassiterite_output ADD COLUMN IF NOT EXISTS exchange_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE cassiterite_output ADD COLUMN IF NOT EXISTS payment_stage VARCHAR(30) NOT NULL DEFAULT 'FULL_SETTLEMENT'")

    op.execute("UPDATE cassiterite_output SET output_amount_rwf = COALESCE(output_amount_rwf, output_amount, 0)")
    op.execute("UPDATE cassiterite_output SET amount_paid_rwf = COALESCE(amount_paid_rwf, amount_paid, 0)")

    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_output_currency ON cassiterite_output (currency)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_output_payment_stage ON cassiterite_output (payment_stage)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_cassiterite_output_payment_stage")
    op.execute("DROP INDEX IF EXISTS ix_cassiterite_output_currency")
    op.execute("DROP INDEX IF EXISTS ix_copper_output_payment_stage")
    op.execute("DROP INDEX IF EXISTS ix_copper_output_currency")

    op.execute("ALTER TABLE cassiterite_output DROP COLUMN IF EXISTS payment_stage")
    op.execute("ALTER TABLE cassiterite_output DROP COLUMN IF EXISTS exchange_rate")
    op.execute("ALTER TABLE cassiterite_output DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE cassiterite_output DROP COLUMN IF EXISTS amount_paid_rwf")
    op.execute("ALTER TABLE cassiterite_output DROP COLUMN IF EXISTS output_amount_rwf")

    op.execute("ALTER TABLE copper_output DROP COLUMN IF EXISTS payment_stage")
    op.execute("ALTER TABLE copper_output DROP COLUMN IF EXISTS exchange_rate")
    op.execute("ALTER TABLE copper_output DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE copper_output DROP COLUMN IF EXISTS amount_paid_rwf")
    op.execute("ALTER TABLE copper_output DROP COLUMN IF EXISTS output_amount_rwf")
