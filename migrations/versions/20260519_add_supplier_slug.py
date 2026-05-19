"""Add supplier_slug column to unified_supplier_advance.

Revision ID: 20260519_add_supplier_slug
Revises: fe97beb6a9d3
Create Date: 2026-05-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = '20260519_add_supplier_slug'
down_revision = 'fe97beb6a9d3'
branch_labels = None
depends_on = None


def upgrade():
    # Add supplier_slug column
    op.add_column(
        'unified_supplier_advance',
        sa.Column('supplier_slug', sa.String(140), nullable=True, index=True)
    )
    
    # Backfill supplier_slug from supplier_name
    # Use SQL to generate slug: lowercase, replace non-alphanumeric with hyphens, collapse multiples
    op.execute("""
        UPDATE unified_supplier_advance
        SET supplier_slug = LOWER(
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(supplier_name, '[^a-z0-9\\s-]', '', 'gi'),
                    '[\\s/]+', '-', 'g'
                ),
                '-+', '-', 'g'
            )
        )
        WHERE supplier_slug IS NULL
    """)
    
    # Trim leading/trailing hyphens
    op.execute("""
        UPDATE unified_supplier_advance
        SET supplier_slug = TRIM(both '-' from supplier_slug)
        WHERE supplier_slug IS NOT NULL
    """)


def downgrade():
    op.drop_column('unified_supplier_advance', 'supplier_slug')
