from app import app, db
from sqlalchemy import text

TABLES = [
    'payment_review',
    'supplier_payment',
    'worker_payment',
    'cassiterite_supplier_payment',
    'cassiterite_worker_payment',
    'copper_stock',
    'cassiterite_stock',
    'alembic_version',
]

with app.app_context():
    q = text(
        """
        select tablename, tableowner
        from pg_tables
        where schemaname = 'public'
          and tablename = any(:tables)
        order by tablename
        """
    )
    rows = db.session.execute(q, {"tables": TABLES}).fetchall()
    for tablename, owner in rows:
        print(f"{tablename}|{owner}")
