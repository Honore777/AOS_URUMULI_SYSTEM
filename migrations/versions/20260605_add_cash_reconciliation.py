"""Add cash reconciliation table.

Revision ID: 20260605_add_cash_reconciliation
Revises: 20260605_merge_heads_unified_supplier
Create Date: 2026-06-05 00:00:00.000000
"""

from alembic import op


revision = '20260605_add_cash_reconciliation'
down_revision = '20260605_merge_heads_unified_supplier'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_reconciliation (
            id SERIAL PRIMARY KEY,
            account_id INTEGER NOT NULL,
            recon_date DATE NOT NULL,

            expected_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            counted_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            variance DOUBLE PRECISION NOT NULL DEFAULT 0.0,

            note TEXT NULL,

            created_by_id INTEGER NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMP NULL,
            deleted_by_id INTEGER NULL,
            delete_reason TEXT NULL,

            CONSTRAINT fk_cash_reconciliation_account_id FOREIGN KEY (account_id) REFERENCES cash_account(id),
            CONSTRAINT fk_cash_reconciliation_created_by_id FOREIGN KEY (created_by_id) REFERENCES "user"(id),
            CONSTRAINT fk_cash_reconciliation_deleted_by_id FOREIGN KEY (deleted_by_id) REFERENCES "user"(id)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_reconciliation_account_id ON cash_reconciliation (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_reconciliation_recon_date ON cash_reconciliation (recon_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_reconciliation_variance ON cash_reconciliation (variance)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_reconciliation_created_at ON cash_reconciliation (created_at)")

    # Enforce one reconciliation per account per day (soft-delete aware via app logic)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = 'uq_cash_reconciliation_account_day'
            ) THEN
                CREATE UNIQUE INDEX uq_cash_reconciliation_account_day
                ON cash_reconciliation (account_id, recon_date)
                WHERE is_deleted = FALSE;
            END IF;
        END $$;
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_cash_reconciliation_account_day")
    op.execute("DROP INDEX IF EXISTS ix_cash_reconciliation_created_at")
    op.execute("DROP INDEX IF EXISTS ix_cash_reconciliation_variance")
    op.execute("DROP INDEX IF EXISTS ix_cash_reconciliation_recon_date")
    op.execute("DROP INDEX IF EXISTS ix_cash_reconciliation_account_id")
    op.execute("DROP TABLE IF EXISTS cash_reconciliation")
