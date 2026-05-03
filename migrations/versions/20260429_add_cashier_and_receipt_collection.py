"""Add cash account tables and receipt collection columns.

Revision ID: 20260429_add_cashier_and_receipt_collection
Revises: 20260427_add_payment_norm_and_expense
Create Date: 2026-04-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260429_add_cashier_and_receipt_collection'
down_revision = '20260427_add_payment_norm_and_expense'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_account (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            opening_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            current_balance DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            created_by_id INTEGER NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_cash_account_created_by_id FOREIGN KEY (created_by_id) REFERENCES "user"(id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cash_transaction (
            id SERIAL PRIMARY KEY,
            account_id INTEGER NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            direction VARCHAR(4) NOT NULL,
            note TEXT NULL,
            created_by_id INTEGER NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_cash_transaction_account_id FOREIGN KEY (account_id) REFERENCES cash_account(id),
            CONSTRAINT fk_cash_transaction_created_by_id FOREIGN KEY (created_by_id) REFERENCES "user"(id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_transaction_account_id ON cash_transaction (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_transaction_created_at ON cash_transaction (created_at)")

    op.execute("ALTER TABLE customer_receipt ADD COLUMN IF NOT EXISTS is_collected BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE customer_receipt ADD COLUMN IF NOT EXISTS collected_by_id INTEGER")
    op.execute("ALTER TABLE customer_receipt ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP NULL")
    op.execute("ALTER TABLE customer_receipt ADD COLUMN IF NOT EXISTS cash_account_id INTEGER")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_receipt_is_collected ON customer_receipt (is_collected)")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name='customer_receipt' AND constraint_name='fk_customer_receipt_collected_by_id'
            ) THEN
                ALTER TABLE customer_receipt
                ADD CONSTRAINT fk_customer_receipt_collected_by_id
                FOREIGN KEY (collected_by_id) REFERENCES "user"(id);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name='customer_receipt' AND constraint_name='fk_customer_receipt_cash_account_id'
            ) THEN
                ALTER TABLE customer_receipt
                ADD CONSTRAINT fk_customer_receipt_cash_account_id
                FOREIGN KEY (cash_account_id) REFERENCES cash_account(id);
            END IF;
        END $$;
        """
    )


def downgrade():
    op.execute("ALTER TABLE customer_receipt DROP CONSTRAINT IF EXISTS fk_customer_receipt_cash_account_id")
    op.execute("ALTER TABLE customer_receipt DROP CONSTRAINT IF EXISTS fk_customer_receipt_collected_by_id")
    op.execute("DROP INDEX IF EXISTS ix_customer_receipt_is_collected")
    op.execute("ALTER TABLE customer_receipt DROP COLUMN IF EXISTS cash_account_id")
    op.execute("ALTER TABLE customer_receipt DROP COLUMN IF EXISTS collected_at")
    op.execute("ALTER TABLE customer_receipt DROP COLUMN IF EXISTS collected_by_id")
    op.execute("ALTER TABLE customer_receipt DROP COLUMN IF EXISTS is_collected")

    op.execute("DROP INDEX IF EXISTS ix_cash_transaction_created_at")
    op.execute("DROP INDEX IF EXISTS ix_cash_transaction_account_id")
    op.execute("DROP TABLE IF EXISTS cash_transaction")
    op.execute("DROP TABLE IF EXISTS cash_account")