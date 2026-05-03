"""Repair missing advance supplier columns and add performance indexes.

Revision ID: 20260426_repair_advance_cols
Revises: 20260426_soft_delete_and_request_payload
Create Date: 2026-04-26 10:40:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260426_repair_advance_cols'
down_revision = '20260426_soft_delete_and_request_payload'
branch_labels = None
depends_on = None


def upgrade():
    # Ensure copper supplier payment advance columns exist.
    op.execute("ALTER TABLE supplier_payment ALTER COLUMN stock_id DROP NOT NULL")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(100)")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS is_advance BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS advance_remaining DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS delete_reason TEXT")

    # Ensure cassiterite supplier payment advance columns exist.
    op.execute("ALTER TABLE cassiterite_supplier_payment ALTER COLUMN stock_id DROP NOT NULL")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(100)")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS is_advance BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS advance_remaining DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS delete_reason TEXT")

    # Indexes for lookups and recent advance pagination.
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_supplier_name ON supplier_payment (supplier_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_is_advance ON supplier_payment (is_advance)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_supplier_payment_advance_recent "
        "ON supplier_payment (paid_at DESC) WHERE is_advance = TRUE AND is_deleted = FALSE"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_supplier_name "
        "ON cassiterite_supplier_payment (supplier_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_is_advance "
        "ON cassiterite_supplier_payment (is_advance)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_advance_recent "
        "ON cassiterite_supplier_payment (paid_at DESC) WHERE is_advance = TRUE AND is_deleted = FALSE"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_cassiterite_supplier_payment_advance_recent")
    op.execute("DROP INDEX IF EXISTS ix_cassiterite_supplier_payment_is_advance")
    op.execute("DROP INDEX IF EXISTS ix_cassiterite_supplier_payment_supplier_name")

    op.execute("DROP INDEX IF EXISTS ix_supplier_payment_advance_recent")
    op.execute("DROP INDEX IF EXISTS ix_supplier_payment_is_advance")
    op.execute("DROP INDEX IF EXISTS ix_supplier_payment_supplier_name")

    op.execute("ALTER TABLE cassiterite_supplier_payment DROP COLUMN IF EXISTS delete_reason")
    op.execute("ALTER TABLE cassiterite_supplier_payment DROP COLUMN IF EXISTS advance_remaining")
    op.execute("ALTER TABLE cassiterite_supplier_payment DROP COLUMN IF EXISTS is_advance")
    op.execute("ALTER TABLE cassiterite_supplier_payment DROP COLUMN IF EXISTS supplier_name")

    op.execute("ALTER TABLE supplier_payment DROP COLUMN IF EXISTS delete_reason")
    op.execute("ALTER TABLE supplier_payment DROP COLUMN IF EXISTS advance_remaining")
    op.execute("ALTER TABLE supplier_payment DROP COLUMN IF EXISTS is_advance")
    op.execute("ALTER TABLE supplier_payment DROP COLUMN IF EXISTS supplier_name")
