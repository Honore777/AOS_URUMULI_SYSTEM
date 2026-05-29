from __future__ import annotations

import json

from app import app
from config import db
from core.models import PaymentReview, TransporterLedger


def main() -> None:
    created = 0
    skipped = 0

    with app.app_context():
        reviews = (
            PaymentReview.query
            .filter(PaymentReview.type.in_(['transporter_advance', 'transporter_payment']))
            .order_by(PaymentReview.id.asc())
            .all()
        )

        for review in reviews:
            try:
                payload = json.loads(review.request_payload or '{}') if review.request_payload else {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            action = (payload.get('action') or '').strip().lower()
            entry_kind = (payload.get('entry_kind') or '').strip().upper()
            transporter_name = (payload.get('transporter_name') or review.customer or '').strip()

            if action != 'pay_transporter' or not transporter_name:
                skipped += 1
                continue

            if TransporterLedger.query.filter_by(payment_review_id=int(review.id)).first():
                skipped += 1
                continue

            amount_input = float(payload.get('amount_input') or review.amount or 0.0)
            currency = (payload.get('currency') or review.currency or 'RWF').strip().upper()
            exchange_rate = float(payload.get('exchange_rate') or 1.0)
            amount_rwf = float(payload.get('amount_rwf') or review.amount or 0.0)
            if entry_kind == 'ADVANCE' or (review.type or '').strip().lower() == 'transporter_advance':
                ledger_amount = abs(amount_rwf)
                entry_type = 'ADVANCE'
            else:
                ledger_amount = -abs(amount_rwf)
                entry_type = entry_kind or 'CASH_PAYMENT'

            ledger = TransporterLedger(
                transporter_name=transporter_name,
                supplier_name=None,
                entry_type=entry_type,
                amount_input=amount_input,
                currency=currency,
                exchange_rate=exchange_rate,
                amount_rwf=ledger_amount,
                source_supplier_deduction_id=None,
                payment_review_id=int(review.id),
                cash_transaction_id=int(review.cash_transaction_id) if review.cash_transaction_id else None,
                is_paid=True,
                paid_at=review.disbursed_at or review.created_at,
                created_by_id=review.disbursed_by_id or review.created_by_id,
                created_at=review.disbursed_at or review.created_at,
                note=payload.get('note') or review.boss_comment or f'Transporter {entry_type.lower()}',
            )
            db.session.add(ledger)
            created += 1

        db.session.commit()
        print({'created': created, 'skipped': skipped})


if __name__ == '__main__':
    main()