"""Backfill missing supplier_slug values on unified_supplier_advance.

Revision ID: 20260524_backfill_missing_supplier_slug
Revises: 20260521_add_unified_supplier_advance_is_historical
Create Date: 2026-05-24 00:00:00.000000
"""

from alembic import op


revision = '20260524_backfill_missing_supplier_slug'
down_revision = '20260521_add_unified_supplier_advance_is_historical'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE unified_supplier_advance
        SET supplier_slug = TRIM(BOTH '-' FROM LOWER(
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(COALESCE(supplier_name, ''), '[^a-zA-Z0-9\\s/]+', '', 'g'),
                    '[\\s/]+', '-', 'g'
                ),
                '-+', '-', 'g'
            )
        ))
        WHERE (supplier_slug IS NULL OR TRIM(supplier_slug) = '')
          AND supplier_name IS NOT NULL
          AND TRIM(supplier_name) <> ''
        """
    )


def downgrade():
    pass