import os
import sys

from dotenv import load_dotenv

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv()

from app import app
from config import db


def _norm(name: str) -> str:
    return ' '.join((name or '').strip().lower().split())


def main():
    from copper.models import SupplierPayment
    from cassiterite.models.payment import CassiteriteSupplierPayment
    from core.models import UnifiedSupplierAdvance

    created = 0
    skipped = 0

    with app.app_context():
        db.session.rollback()

        copper_advances = (
            SupplierPayment.query
            .filter(
                SupplierPayment.is_advance.is_(True),
                SupplierPayment.is_deleted.is_(False),
            )
            .order_by(SupplierPayment.paid_at.asc(), SupplierPayment.id.asc())
            .all()
        )
        cass_advances = (
            CassiteriteSupplierPayment.query
            .filter(
                CassiteriteSupplierPayment.is_advance.is_(True),
                CassiteriteSupplierPayment.is_deleted.is_(False),
            )
            .order_by(CassiteriteSupplierPayment.paid_at.asc(), CassiteriteSupplierPayment.id.asc())
            .all()
        )

        for p in copper_advances:
            supplier_name = (p.supplier_name or (p.supplier.name if getattr(p, 'supplier', None) else None) or '').strip()
            if not supplier_name:
                skipped += 1
                continue
            exists = UnifiedSupplierAdvance.query.filter_by(source_mineral_type='copper', source_payment_id=int(p.id)).first()
            if exists:
                skipped += 1
                continue
            row = UnifiedSupplierAdvance(
                supplier_name=supplier_name,
                supplier_name_norm=_norm(supplier_name),
                source_mineral_type='copper',
                source_payment_id=int(p.id),
                input_amount=float(p.input_amount) if p.input_amount is not None else None,
                currency=(p.currency or 'RWF'),
                exchange_rate=float(p.exchange_rate or 1.0),
                amount_rwf=float(p.amount_rwf or 0.0),
                paid_at=p.paid_at,
                method=p.method,
                reference=p.reference,
                note=p.note,
                advance_remaining=float(p.advance_remaining or 0.0),
                created_by_id=getattr(p, 'created_by_id', None),
            )
            db.session.add(row)
            created += 1

        for p in cass_advances:
            supplier_name = (p.supplier_name or (p.supplier.name if getattr(p, 'supplier', None) else None) or '').strip()
            if not supplier_name:
                skipped += 1
                continue
            exists = UnifiedSupplierAdvance.query.filter_by(source_mineral_type='cassiterite', source_payment_id=int(p.id)).first()
            if exists:
                skipped += 1
                continue
            row = UnifiedSupplierAdvance(
                supplier_name=supplier_name,
                supplier_name_norm=_norm(supplier_name),
                source_mineral_type='cassiterite',
                source_payment_id=int(p.id),
                input_amount=float(p.input_amount) if p.input_amount is not None else None,
                currency=(p.currency or 'RWF'),
                exchange_rate=float(p.exchange_rate or 1.0),
                amount_rwf=float(p.amount_rwf or 0.0),
                paid_at=p.paid_at,
                method=p.method,
                reference=p.reference,
                note=p.note,
                advance_remaining=float(p.advance_remaining or 0.0),
                created_by_id=getattr(p, 'created_by_id', None),
            )
            db.session.add(row)
            created += 1

        db.session.commit()

    print(f"Created unified advances: {created}")
    print(f"Skipped: {skipped}")


if __name__ == '__main__':
    main()
