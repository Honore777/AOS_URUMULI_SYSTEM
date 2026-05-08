"""Add disbursement fields to payment_review.

Revision ID: 20260505_add_paymentreview_disbursement_fields
Revises: 20260605_merge_heads_unified_supplier
Create Date: 2026-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260505_add_paymentreview_disbursement_fields'
down_revision = '20260605_merge_heads_unified_supplier'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment_review', schema=None) as batch_op:
        batch_op.add_column(sa.Column('disbursement_status', sa.String(length=20), nullable=False, server_default=sa.text("'NOT_DISBURSED'")))
        batch_op.add_column(sa.Column('disbursed_by_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('disbursed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cash_account_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('cash_transaction_id', sa.Integer(), nullable=True))

    op.execute("CREATE INDEX IF NOT EXISTS ix_payment_review_disbursement_status ON payment_review (disbursement_status)")

    # Best-effort FK creation (Postgres-style guard)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name='payment_review' AND constraint_name='fk_payment_review_disbursed_by_id'
            ) THEN
                ALTER TABLE payment_review
                ADD CONSTRAINT fk_payment_review_disbursed_by_id
                FOREIGN KEY (disbursed_by_id) REFERENCES "user"(id);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name='payment_review' AND constraint_name='fk_payment_review_cash_account_id'
            ) THEN
                ALTER TABLE payment_review
                ADD CONSTRAINT fk_payment_review_cash_account_id
                FOREIGN KEY (cash_account_id) REFERENCES cash_account(id);
            END IF;
        END $$;
        """
    )


def downgrade():
    op.execute("ALTER TABLE payment_review DROP CONSTRAINT IF EXISTS fk_payment_review_cash_account_id")
    op.execute("ALTER TABLE payment_review DROP CONSTRAINT IF EXISTS fk_payment_review_disbursed_by_id")
    op.execute("DROP INDEX IF EXISTS ix_payment_review_disbursement_status")

    with op.batch_alter_table('payment_review', schema=None) as batch_op:
        batch_op.drop_column('cash_transaction_id')
        batch_op.drop_column('cash_account_id')
        batch_op.drop_column('disbursed_at')
        batch_op.drop_column('disbursed_by_id')
        batch_op.drop_column('disbursement_status')
