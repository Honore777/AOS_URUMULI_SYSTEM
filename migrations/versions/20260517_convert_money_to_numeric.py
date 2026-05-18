"""Convert key monetary columns from Float to Numeric(18,2)

Revision ID: 20260517_convert_money_to_numeric
Revises: 001_add_receipt_tracking
Create Date: 2026-05-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260517_convert_money_to_numeric'
down_revision = '001_add_receipt_tracking'
branch_labels = None
depends_on = None


def upgrade():
    # customer receipts
    op.alter_column('customer_receipt', 'amount_input', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount_input::numeric,2)")
    op.alter_column('customer_receipt', 'amount_rwf', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount_rwf::numeric,2)")

    # copper supplier payments table (app's copper module)
    op.alter_column('supplier_payment', 'amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount::numeric,2)")
    op.alter_column('supplier_payment', 'input_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(input_amount::numeric,2)")
    op.alter_column('supplier_payment', 'amount_rwf', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount_rwf::numeric,2)")
    op.alter_column('supplier_payment', 'advance_remaining', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(advance_remaining::numeric,2)")

    op.alter_column('copper_advance_allocation', 'applied_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(applied_amount::numeric,2)")

    # cassiterite supplier payments
    op.alter_column('cassiterite_supplier_payment', 'amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount::numeric,2)")
    op.alter_column('cassiterite_supplier_payment', 'input_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(input_amount::numeric,2)")
    op.alter_column('cassiterite_supplier_payment', 'amount_rwf', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount_rwf::numeric,2)")
    op.alter_column('cassiterite_supplier_payment', 'advance_remaining', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(advance_remaining::numeric,2)")

    op.alter_column('cassiterite_advance_allocation', 'applied_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(applied_amount::numeric,2)")

    # unified supplier advances
    op.alter_column('unified_supplier_advance', 'input_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(input_amount::numeric,2)")
    op.alter_column('unified_supplier_advance', 'amount_rwf', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(amount_rwf::numeric,2)")
    op.alter_column('unified_supplier_advance', 'advance_remaining', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(advance_remaining::numeric,2)")

    op.alter_column('unified_supplier_advance_allocation', 'applied_amount', type_=sa.Numeric(18,2), existing_type=sa.Float(), postgresql_using="ROUND(applied_amount::numeric,2)")


def downgrade():
    # revert back to float (double precision)
    op.alter_column('unified_supplier_advance_allocation', 'applied_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(applied_amount::double precision)")
    op.alter_column('unified_supplier_advance', 'advance_remaining', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(advance_remaining::double precision)")
    op.alter_column('unified_supplier_advance', 'amount_rwf', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount_rwf::double precision)")
    op.alter_column('unified_supplier_advance', 'input_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(input_amount::double precision)")

    op.alter_column('cassiterite_advance_allocation', 'applied_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(applied_amount::double precision)")
    op.alter_column('cassiterite_supplier_payment', 'advance_remaining', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(advance_remaining::double precision)")
    op.alter_column('cassiterite_supplier_payment', 'amount_rwf', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount_rwf::double precision)")
    op.alter_column('cassiterite_supplier_payment', 'input_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(input_amount::double precision)")
    op.alter_column('cassiterite_supplier_payment', 'amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount::double precision)")

    op.alter_column('copper_advance_allocation', 'applied_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(applied_amount::double precision)")
    op.alter_column('supplier_payment', 'advance_remaining', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(advance_remaining::double precision)")
    op.alter_column('supplier_payment', 'amount_rwf', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount_rwf::double precision)")
    op.alter_column('supplier_payment', 'input_amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(input_amount::double precision)")
    op.alter_column('supplier_payment', 'amount', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount::double precision)")

    op.alter_column('customer_receipt', 'amount_rwf', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount_rwf::double precision)")
    op.alter_column('customer_receipt', 'amount_input', type_=sa.Float(), existing_type=sa.Numeric(18,2), postgresql_using="(amount_input::double precision)")