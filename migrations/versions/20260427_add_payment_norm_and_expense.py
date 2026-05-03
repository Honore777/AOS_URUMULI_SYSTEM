"""Add supplier payment normalization columns and expense_transaction table.

Revision ID: 20260427_add_payment_norm_and_expense
Revises: 20260426_add_output_rwf_columns
Create Date: 2026-04-27 10:05:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260427_add_payment_norm_and_expense'
down_revision = '20260426_add_output_rwf_columns'
branch_labels = None
depends_on = None


def upgrade():
    # Supplier master tables used by supplier_id foreign keys.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS copper_supplier (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            phone VARCHAR(30),
            email VARCHAR(120),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMP NULL,
            deleted_by_id INTEGER NULL,
            delete_reason TEXT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_supplier_name ON copper_supplier (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_supplier_is_active ON copper_supplier (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_copper_supplier_is_deleted ON copper_supplier (is_deleted)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cassiterite_supplier (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            phone VARCHAR(30),
            email VARCHAR(120),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMP NULL,
            deleted_by_id INTEGER NULL,
            delete_reason TEXT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_name ON cassiterite_supplier (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_is_active ON cassiterite_supplier (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_is_deleted ON cassiterite_supplier (is_deleted)")

    # Copper supplier_payment normalization/lifecycle fields.
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS supplier_id INTEGER")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS input_amount DOUBLE PRECISION")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'RWF'")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS exchange_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS amount_rwf DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS payment_type VARCHAR(20) NOT NULL DEFAULT 'SETTLEMENT'")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS disbursement_status VARCHAR(20) NOT NULL DEFAULT 'DISBURSED'")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS approved_by_id INTEGER")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS disbursed_by_id INTEGER")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS disbursed_at TIMESTAMP")
    op.execute("ALTER TABLE supplier_payment ADD COLUMN IF NOT EXISTS created_by_id INTEGER")
    op.execute("UPDATE supplier_payment SET amount_rwf = COALESCE(amount_rwf, amount, 0)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_supplier_id ON supplier_payment (supplier_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_currency ON supplier_payment (currency)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_amount_rwf ON supplier_payment (amount_rwf)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_payment_type ON supplier_payment (payment_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_approval_status ON supplier_payment (approval_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_disbursement_status ON supplier_payment (disbursement_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_supplier_payment_created_by_id ON supplier_payment (created_by_id)")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_name='supplier_payment'
                  AND constraint_name='fk_supplier_payment_supplier_id'
            ) THEN
                ALTER TABLE supplier_payment
                ADD CONSTRAINT fk_supplier_payment_supplier_id
                FOREIGN KEY (supplier_id) REFERENCES copper_supplier(id);
            END IF;
        END $$;
        """
    )

    # Cassiterite supplier_payment normalization/lifecycle fields.
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS supplier_id INTEGER")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS input_amount DOUBLE PRECISION")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'RWF'")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS exchange_rate DOUBLE PRECISION NOT NULL DEFAULT 1.0")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS amount_rwf DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS payment_type VARCHAR(20) NOT NULL DEFAULT 'SETTLEMENT'")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS disbursement_status VARCHAR(20) NOT NULL DEFAULT 'DISBURSED'")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS approved_by_id INTEGER")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS disbursed_by_id INTEGER")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS disbursed_at TIMESTAMP")
    op.execute("ALTER TABLE cassiterite_supplier_payment ADD COLUMN IF NOT EXISTS created_by_id INTEGER")
    op.execute("UPDATE cassiterite_supplier_payment SET amount_rwf = COALESCE(amount_rwf, amount, 0)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_supplier_id ON cassiterite_supplier_payment (supplier_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_currency ON cassiterite_supplier_payment (currency)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_amount_rwf ON cassiterite_supplier_payment (amount_rwf)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_payment_type ON cassiterite_supplier_payment (payment_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_approval_status ON cassiterite_supplier_payment (approval_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_disbursement_status ON cassiterite_supplier_payment (disbursement_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cassiterite_supplier_payment_created_by_id ON cassiterite_supplier_payment (created_by_id)")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_name='cassiterite_supplier_payment'
                  AND constraint_name='fk_cassiterite_supplier_payment_supplier_id'
            ) THEN
                ALTER TABLE cassiterite_supplier_payment
                ADD CONSTRAINT fk_cassiterite_supplier_payment_supplier_id
                FOREIGN KEY (supplier_id) REFERENCES cassiterite_supplier(id);
            END IF;
        END $$;
        """
    )

    # Generic expense transaction table used by worker payment aliases.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS expense_transaction (
            id SERIAL PRIMARY KEY,
            category VARCHAR(20) NOT NULL DEFAULT 'OTHER',
            worker_name VARCHAR(120),
            payee_name VARCHAR(120),
            description TEXT,
            amount DOUBLE PRECISION NOT NULL,
            currency VARCHAR(10) NOT NULL DEFAULT 'RWF',
            method VARCHAR(20),
            reference VARCHAR(100),
            note TEXT,
            paid_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            mineral_type VARCHAR(20),
            approval_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            approved_by_id INTEGER,
            approved_at TIMESTAMP,
            disbursement_status VARCHAR(20) NOT NULL DEFAULT 'NOT_DISBURSED',
            disbursed_by_id INTEGER,
            disbursed_at TIMESTAMP,
            created_by_id INTEGER,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMP,
            deleted_by_id INTEGER,
            delete_reason TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_category ON expense_transaction (category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_worker_name ON expense_transaction (worker_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_payee_name ON expense_transaction (payee_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_paid_at ON expense_transaction (paid_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_mineral_type ON expense_transaction (mineral_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_approval_status ON expense_transaction (approval_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_disbursement_status ON expense_transaction (disbursement_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_created_at ON expense_transaction (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_expense_transaction_is_deleted ON expense_transaction (is_deleted)")


def downgrade():
    # Keep downgrade conservative: drop only the newly created table/indexes.
    op.execute("DROP TABLE IF EXISTS expense_transaction")
