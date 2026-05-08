"""Add currency fields to cash tables.

Revision ID: 20260506_add_currency_to_cash_tables
Revises: 20260505_add_paymentreview_disbursement_fields
Create Date: 2026-05-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260506_add_currency_to_cash_tables'
down_revision = '20260505_add_paymentreview_disbursement_fields'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cash_account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('currency', sa.String(length=10), nullable=False, server_default=sa.text("'RWF'")))

    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_account_currency ON cash_account (currency)")

    with op.batch_alter_table('cash_transaction', schema=None) as batch_op:
        batch_op.add_column(sa.Column('currency', sa.String(length=10), nullable=False, server_default=sa.text("'RWF'")))
        batch_op.add_column(sa.Column('exchange_rate', sa.Float(), nullable=False, server_default=sa.text('1.0')))
        batch_op.add_column(sa.Column('amount_input', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('amount_rwf', sa.Float(), nullable=True))

    op.execute("CREATE INDEX IF NOT EXISTS ix_cash_transaction_currency ON cash_transaction (currency)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_cash_transaction_currency")
    op.execute("DROP INDEX IF EXISTS ix_cash_account_currency")

    with op.batch_alter_table('cash_transaction', schema=None) as batch_op:
        batch_op.drop_column('amount_rwf')
        batch_op.drop_column('amount_input')
        batch_op.drop_column('exchange_rate')
        batch_op.drop_column('currency')

    with op.batch_alter_table('cash_account', schema=None) as batch_op:
        batch_op.drop_column('currency')
