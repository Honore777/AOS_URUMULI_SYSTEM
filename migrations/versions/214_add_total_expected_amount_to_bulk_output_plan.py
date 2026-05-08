"""add total_expected_amount to bulk_output_plan

Revision ID: 214_add_total_expected_amount
Revises: 8ef07dead9e6
Create Date: 2026-04-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '214_add_total_expected_amount'
down_revision = '8ef07dead9e6'
branch_labels = None
depends_on = None


def upgrade():
    # Add total_expected_amount column to bulk_output_plan table
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='bulk_output_plan'
                  AND column_name='total_expected_amount'
            ) THEN
                ALTER TABLE bulk_output_plan
                ADD COLUMN total_expected_amount DOUBLE PRECISION DEFAULT 0;
            END IF;
        END $$;
        """
    )


def downgrade():
    # Remove the column if migration is rolled back
    op.execute("ALTER TABLE bulk_output_plan DROP COLUMN IF EXISTS total_expected_amount")
