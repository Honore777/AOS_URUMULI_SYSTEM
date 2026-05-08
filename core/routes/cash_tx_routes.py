import logging

from flask import render_template, abort
from flask_login import current_user

from core.auth import role_required
from core.models import CashTransaction, CashAccount, User

from . import core_bp

logger = logging.getLogger(__name__)


@core_bp.route('/cashier/transactions/<int:tx_id>')
@role_required('cashier', 'boss', 'admin', 'accountant')
def cash_transaction_detail(tx_id: int):
    tx = CashTransaction.query.get_or_404(tx_id)
    account = CashAccount.query.get(tx.account_id) if getattr(tx, 'account_id', None) else None
    created_by = User.query.get(tx.created_by_id) if getattr(tx, 'created_by_id', None) else None

    siblings = []
    try:
        if getattr(tx, 'reference', None):
            siblings = (
                CashTransaction.query
                .filter(CashTransaction.reference == tx.reference, CashTransaction.id != tx.id)
                .order_by(CashTransaction.created_at.asc())
                .limit(20)
                .all()
            )
    except Exception:
        siblings = []

    return render_template(
        'cashier/transaction_detail.html',
        tx=tx,
        account=account,
        created_by=created_by,
        siblings=siblings,
    )
