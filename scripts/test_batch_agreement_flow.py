from datetime import datetime
import json
from config import db
from core.models import (
    User,
    BulkOutputPlan,
    PaymentReview,
    PaymentReviewStatus,
    BatchDeduction,
    BulkPlanStatus,
)
from werkzeug.security import generate_password_hash

# helper to get or create user
def get_or_create_user(username, role):
    u = User.query.filter_by(username=username).first()
    if u:
        return u
    u = User(username=username, email=f"{username}@example.com", password_hash=generate_password_hash('pass'), role=role)
    db.session.add(u)
    db.session.commit()
    return u

neg = get_or_create_user('test_negotiator', 'negotiator')
boss = get_or_create_user('test_boss', 'boss')

# create a BulkOutputPlan
plan = BulkOutputPlan(
    mineral_type='copper',
    created_by_id=neg.id,
    created_at=datetime.utcnow(),
    status=BulkPlanStatus.STOCK_CONFIRMED.value,
    customer=None,
    batch_id='TEST-BATCH-123',
    total_expected_amount=0,
)
db.session.add(plan)
db.session.flush()
print('Created plan id', plan.id)

# Build payload for PaymentReview (USD agreement with deductions)
payload = {
    'action': 'batch_agreement',
    'plan_id': int(plan.id),
    'batch_id': plan.batch_id,
    'mineral_type': plan.mineral_type,
    'customer': 'ACME Corp',
    'total_expected_amount': 1000.0,
    'currency': 'USD',
    'exchange_rate': 1200.0,
    'deductions': [
        {'type': 'RMA', 'amount': 50.0},
        {'type': 'TRANSPORT', 'amount': 25.0},
        {'type': 'ALEX_FEE', 'amount': 10.0},
    ],
}

review = PaymentReview(
    mineral_type=plan.mineral_type,
    type='batch_agreement',
    customer=payload['customer'],
    amount=float(payload['total_expected_amount']),
    currency=payload['currency'],
    created_by_id=neg.id,
    status=PaymentReviewStatus.PENDING_REVIEW.value,
    request_payload=json.dumps(payload),
)

db.session.add(review)
db.session.flush()
print('Created PaymentReview id', review.id)

# Simulate boss approval processing (DB-level) -- similar to boss_approve_payment logic
try:
    plan.customer = payload.get('customer')
    plan.total_expected_amount = float(payload.get('total_expected_amount') or 0)
    plan.currency = (payload.get('currency') or 'RWF').upper()
    plan.exchange_rate = float(payload.get('exchange_rate') or 1.0)

    ded_list = payload.get('deductions') or []
    for d in ded_list:
        d_type = (d.get('type') or '').strip().upper()
        try:
            amt = float(d.get('amount') or 0)
        except Exception:
            amt = 0.0
        if not d_type or amt <= 0:
            continue
        bd = BatchDeduction(
            batch_id=int(plan.id),
            deduction_type=d_type,
            amount_input=amt,
            currency=plan.currency,
            exchange_rate=plan.exchange_rate,
            amount_rwf=amt * float(plan.exchange_rate or 1.0),
            created_by_id=neg.id,
            created_at=datetime.utcnow(),
        )
        db.session.add(bd)

    review.status = PaymentReviewStatus.APPROVED.value
    review.reviewed_by_id = boss.id
    review.reviewed_at = datetime.utcnow()

    db.session.add(plan)
    db.session.add(review)
    db.session.commit()
    print('Simulated boss approval and committed.')
except Exception as e:
    db.session.rollback()
    print('Error during simulated approval:', e)

# Query and print BatchDeduction rows for this plan
rows = BatchDeduction.query.filter_by(batch_id=plan.id).all()
print('BatchDeduction rows:')
for r in rows:
    print('-', r.id, r.deduction_type, float(r.amount_input), r.currency, float(r.amount_rwf))
