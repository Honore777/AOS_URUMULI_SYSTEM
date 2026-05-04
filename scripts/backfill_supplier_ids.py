"""Backfill supplier_id on supplier payment rows.

This script repairs historical rows created when supplier_id was intentionally
left NULL during an early advance-payment iteration.

It assigns:
- supplier_id from linked stock.supplier when stock_id is present
- otherwise supplier_id from supplier_name (case-insensitive match)

It only updates rows where supplier_id is currently NULL.

Run (PowerShell):
  python scripts/backfill_supplier_ids.py

Note: This script assumes your Flask app can be imported and DB session is
available via config.db. It does not create or drop tables.
"""

import sys
from pathlib import Path


def _normalize_name(value) -> str:
    return (value or "").strip()


def backfill_copper() -> dict:
    from sqlalchemy import func

    from config import db
    from copper.models import CopperSupplier, SupplierPayment, CopperStock

    updated = 0
    skipped = 0
    created_suppliers = 0

    rows = (
        SupplierPayment.query
        .filter(SupplierPayment.supplier_id.is_(None))
        .filter(SupplierPayment.is_deleted.is_(False))
        .all()
    )

    for p in rows:
        supplier_name = None
        if p.stock_id:
            stock = db.session.get(CopperStock, p.stock_id)
            supplier_name = _normalize_name(getattr(stock, "supplier", None)) if stock else ""
        if not supplier_name:
            supplier_name = _normalize_name(getattr(p, "supplier_name", None))

        if not supplier_name:
            skipped += 1
            continue

        supplier = CopperSupplier.query.filter(func.lower(CopperSupplier.name) == supplier_name.lower()).first()
        if not supplier:
            supplier = CopperSupplier(name=supplier_name)
            db.session.add(supplier)
            db.session.flush()
            created_suppliers += 1

        p.supplier_id = int(supplier.id)
        db.session.add(p)
        updated += 1

    db.session.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "created_suppliers": created_suppliers,
        "total_null_supplier_id_rows": len(rows),
    }


def backfill_cassiterite() -> dict:
    from sqlalchemy import func

    from config import db
    from cassiterite.models import CassiteriteSupplier, CassiteriteSupplierPayment, CassiteriteStock

    updated = 0
    skipped = 0
    created_suppliers = 0

    rows = (
        CassiteriteSupplierPayment.query
        .filter(CassiteriteSupplierPayment.supplier_id.is_(None))
        .filter(CassiteriteSupplierPayment.is_deleted.is_(False))
        .all()
    )

    for p in rows:
        supplier_name = None
        if p.stock_id:
            stock = db.session.get(CassiteriteStock, p.stock_id)
            supplier_name = _normalize_name(getattr(stock, "supplier", None)) if stock else ""
        if not supplier_name:
            supplier_name = _normalize_name(getattr(p, "supplier_name", None))

        if not supplier_name:
            skipped += 1
            continue

        supplier = CassiteriteSupplier.query.filter(func.lower(CassiteriteSupplier.name) == supplier_name.lower()).first()
        if not supplier:
            supplier = CassiteriteSupplier(name=supplier_name)
            db.session.add(supplier)
            db.session.flush()
            created_suppliers += 1

        p.supplier_id = int(supplier.id)
        db.session.add(p)
        updated += 1

    db.session.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "created_suppliers": created_suppliers,
        "total_null_supplier_id_rows": len(rows),
    }


def main() -> int:
    # Ensure project root is on sys.path when running as `python scripts/...`.
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Import app to ensure models and db are initialized.
    import app as app_module

    flask_app = getattr(app_module, "app", None)
    if flask_app is None:
        raise RuntimeError("Could not find Flask app object `app` in app.py")

    with flask_app.app_context():
        print("Backfilling copper supplier payments...")
        copper_stats = backfill_copper()
        print(copper_stats)

        print("Backfilling cassiterite supplier payments...")
        cass_stats = backfill_cassiterite()
        print(cass_stats)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
