#!/usr/bin/env python
from app import db, app
from core.models import PaymentReview
from datetime import datetime

ctx = app.app_context()
ctx.push()

r = db.session.query(PaymentReview).filter_by(id=106).first()
if r:
    r.status = 'APPROVED'
    r.approved_at = datetime.utcnow()
    db.session.commit()
    print('✓ Review 106 approved')
else:
    print('✗ Review not found')
