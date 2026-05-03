# Quick script to check database tables
import sys
sys.path.insert(0, '.')

from app import app, db
from sqlalchemy import text

with app.app_context():
    # Check tables
    result = db.session.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"))
    print("=== Tables in database ===")
    for row in result:
        print(row[0])
    
    print("\n=== Checking for new columns ===")
    tables = ['supplier_payment', 'worker_payment', 'cassiterite_supplier_payment', 'cassiterite_worker_payment']
    new_cols = ['is_deleted', 'deleted_at', 'deleted_by_id', 'delete_reason', 'is_advance', 'supplier_name', 'advance_remaining']
    
    for table in tables:
        try:
            cols_str = ','.join(["'" + c + "'" for c in new_cols])
            result = db.session.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' AND column_name IN ({cols_str})"))
            cols = [row[0] for row in result]
            if cols:
                print(f"{table}: {cols}")
            else:
                print(f"{table}: no new columns found")
        except Exception as e:
            print(f"{table}: error - {e}")