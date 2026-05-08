"""Add unified supplier advance tables.

Revision ID: 20260605_add_unified_supplier_advances
Revises: 20260603_add_cassiterite_advance_allocations
Create Date: 2026-06-05 00:00:00.000000
"""

from alembic import op


revision = '20260605_add_unified_supplier_advances'
down_revision = '20260603_add_cassiterite_advance_allocations'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS unified_supplier_advance (
            id SERIAL PRIMARY KEY,
            supplier_name VARCHAR(120) NOT NULL,
            supplier_name_norm VARCHAR(140) NOT NULL,

            source_mineral_type VARCHAR(20) NULL,
            source_payment_id INTEGER NULL,

            input_amount DOUBLE PRECISION NULL,
            currency VARCHAR(10) NOT NULL DEFAULT 'RWF',
            exchange_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            amount_rwf DOUBLE PRECISION NOT NULL DEFAULT 0.0,

            paid_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            method VARCHAR(50) NULL,
            reference VARCHAR(100) NULL,
            note TEXT NULL,

            advance_remaining DOUBLE PRECISION NOT NULL DEFAULT 0.0,

            created_by_id INTEGER NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMP NULL,
            deleted_by_id INTEGER NULL,
            delete_reason TEXT NULL,

            CONSTRAINT fk_unified_supplier_advance_created_by FOREIGN KEY (created_by_id) REFERENCES "user"(id),
            CONSTRAINT fk_unified_supplier_advance_deleted_by FOREIGN KEY (deleted_by_id) REFERENCES "user"(id)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_supplier_name ON unified_supplier_advance (supplier_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_supplier_name_norm ON unified_supplier_advance (supplier_name_norm)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_paid_at ON unified_supplier_advance (paid_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_advance_remaining ON unified_supplier_advance (advance_remaining)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_source_mineral_type ON unified_supplier_advance (source_mineral_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_source_payment_id ON unified_supplier_advance (source_payment_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS unified_supplier_advance_allocation (
            id SERIAL PRIMARY KEY,
            advance_id INTEGER NOT NULL,
            stock_mineral_type VARCHAR(20) NOT NULL,
            stock_id INTEGER NOT NULL,
            applied_amount DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT fk_unified_supplier_advance_allocation_advance FOREIGN KEY (advance_id) REFERENCES unified_supplier_advance(id)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_allocation_advance_id ON unified_supplier_advance_allocation (advance_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_allocation_stock_mineral_type ON unified_supplier_advance_allocation (stock_mineral_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_unified_supplier_advance_allocation_stock_id ON unified_supplier_advance_allocation (stock_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_allocation_stock_id")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_allocation_stock_mineral_type")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_allocation_advance_id")
    op.execute("DROP TABLE IF EXISTS unified_supplier_advance_allocation")

    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_source_payment_id")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_source_mineral_type")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_advance_remaining")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_paid_at")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_supplier_name_norm")
    op.execute("DROP INDEX IF EXISTS ix_unified_supplier_advance_supplier_name")
    op.execute("DROP TABLE IF EXISTS unified_supplier_advance")
