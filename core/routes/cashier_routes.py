import logging
from datetime import datetime
import json

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user

from config import db
from core.auth import role_required
from core.models import CashAccount, CashTransaction, CustomerReceipt, PaymentReview, PaymentReviewStatus, create_notification, User

from . import core_bp

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return float(default)
        if isinstance(val, str) and not val.strip():
            return float(default)
        return float(val)
    except Exception:
        return float(default)


def _money_from_payload(payload: dict, review=None):
    """Return (currency, exchange_rate, amount_input, amount_rwf, amount_cash).

    - `amount_input`: in `currency` (e.g. USD)
    - `amount_rwf`: reporting amount in RWF
    - `amount_cash`: amount that must hit the cash account (same unit as `currency`)
    """
    if not isinstance(payload, dict):
        payload = {}

    currency = (payload.get('currency') or getattr(review, 'currency', None) or 'RWF').strip().upper()
    exchange_rate = _safe_float(payload.get('exchange_rate') or 1.0, 1.0)

    raw_amount_input = payload.get('amount_input')
    raw_amount_rwf = payload.get('amount_rwf')
    raw_amount = payload.get('amount')
    review_amount = getattr(review, 'amount', 0.0) if review is not None else 0.0

    if currency == 'USD':
        amount_input = _safe_float(raw_amount_input, 0.0)
        if amount_input <= 0 and raw_amount_rwf in (None, '', 0, 0.0):
            # For some payloads, `amount` is the USD input amount.
            amount_input = _safe_float(raw_amount, 0.0)
        if amount_input <= 0:
            amount_input = _safe_float(review_amount, 0.0)

        amount_rwf = _safe_float(raw_amount_rwf, 0.0)
        if amount_rwf <= 0 and exchange_rate > 0 and amount_input > 0:
            amount_rwf = float(amount_input) * float(exchange_rate)

        amount_cash = float(amount_input)
        return currency, float(exchange_rate), float(amount_input), float(amount_rwf), float(amount_cash)

    # Default: RWF
    amount_rwf = _safe_float(raw_amount_rwf, 0.0)
    if amount_rwf <= 0:
        amount_rwf = _safe_float(raw_amount, 0.0)
    if amount_rwf <= 0:
        amount_rwf = _safe_float(review_amount, 0.0)

    amount_input = _safe_float(raw_amount_input, 0.0)
    if amount_input <= 0:
        amount_input = float(amount_rwf)

    amount_cash = float(amount_rwf)
    return currency, float(exchange_rate), float(amount_input), float(amount_rwf), float(amount_cash)


def _cashier_accounts_context():
    accounts = CashAccount.query.order_by(CashAccount.name).all()
    recon_map = {}
    try:
        from core.models import CashReconciliation
        today = datetime.utcnow().date()
        rows = (
            db.session.query(CashReconciliation.account_id)
            .filter(
                CashReconciliation.is_deleted.is_(False),
                CashReconciliation.recon_date == today,
            )
            .all()
        )
        recon_map = {int(r[0]): True for r in rows}
    except Exception:
        recon_map = {}
    return accounts, recon_map


@core_bp.route("/cashier/cash-accounts", methods=["GET", "POST"])
@role_required("boss", "admin")
def cashier_cash_accounts():
    if request.method == "POST":
        name = (request.form.get('name') or '').strip()
        currency = (request.form.get('currency') or 'RWF').strip().upper()
        try:
            opening_balance = float(request.form.get('opening_balance') or 0.0)
        except Exception:
            opening_balance = -1.0

        if not name:
            flash('Cash account name is required.', 'danger')
            return redirect(url_for('core.cashier_cash_accounts'))
        if opening_balance < 0:
            flash('Opening balance must be a valid number >= 0.', 'danger')
            return redirect(url_for('core.cashier_cash_accounts'))
        if currency not in {'RWF', 'USD'}:
            flash('Currency must be RWF or USD.', 'danger')
            return redirect(url_for('core.cashier_cash_accounts'))

        reason = (request.form.get('reason') or '').strip()
        if not reason:
            flash('Reason is required for cash account creation.', 'danger')
            return redirect(url_for('core.cashier_cash_accounts'))

        existing = CashAccount.query.filter(CashAccount.name == name).first()
        if existing:
            flash('A cash account with that name already exists.', 'warning')
            return redirect(url_for('core.cashier_cash_accounts'))

        account = CashAccount(
            name=name,
            currency=currency,
            opening_balance=float(opening_balance),
            current_balance=float(opening_balance),
            create_reason=reason,
            created_by_id=getattr(current_user, 'id', None),
        )
        db.session.add(account)
        db.session.flush()

        # Notify all active bosses in Kinyarwanda.
        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='CASH_ACCOUNT_CREATED',
                message=(
                    f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakoze konti nshya ya cash: "
                    f"{account.name} ({account.currency}) ifite opening balance {float(account.opening_balance or 0):,.2f}. "
                    f"Impamvu: {reason}."
                ),
                related_type='cash_account',
                related_id=int(account.id),
            )

        # Notify all active cashiers that the account is now available.
        cashier_rows = db.session.query(User.id).filter_by(role='cashier', is_active=True).all()
        for (cashier_id,) in cashier_rows:
            create_notification(
                user_id=int(cashier_id),
                type_='CASH_ACCOUNT_READY',
                message=(
                    f"Konti y'amafaranga yashyizweho: {account.name} ({account.currency}). "
                    f"Impamvu: {reason}."
                ),
                related_type='cash_account',
                related_id=int(account.id),
            )
        db.session.commit()
        flash("Konti y'amafaranga yakozwe.", 'success')
        return redirect(url_for('core.cashier_cash_accounts'))

    accounts = CashAccount.query.order_by(CashAccount.currency.asc(), CashAccount.name.asc()).all()
    if getattr(current_user, 'role', None) == 'boss':
        return render_template('boss/cash_accounts.html', accounts=accounts)
    return render_template('cashier/cash_accounts.html', accounts=accounts)


@core_bp.route('/cashier/cash-accounts/request', methods=['GET', 'POST'])
@role_required('cashier')
def cashier_request_cash_account():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        currency = (request.form.get('currency') or 'RWF').strip().upper()
        reason = (request.form.get('reason') or '').strip()
        note = (request.form.get('note') or '').strip()

        if not name:
            flash("Izina rya konti rirakenewe.", 'danger')
            return redirect(url_for('core.cashier_request_cash_account'))
        if currency not in {'RWF', 'USD'}:
            flash("Hitamo ifaranga: RWF cyangwa USD.", 'danger')
            return redirect(url_for('core.cashier_request_cash_account'))
        if not reason:
            flash("Impamvu yo gusaba konti irakenewe.", 'danger')
            return redirect(url_for('core.cashier_request_cash_account'))

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='CASH_ACCOUNT_REQUESTED',
                message=(
                    f"Umubitsi {getattr(current_user, 'username', 'unknown')} arasaba konti y'amafaranga. "
                    f"Konti: {name} ({currency}). Impamvu: {reason}." + (f" Ibisobanuro: {note}." if note else "")
                ),
                related_type='cash_account_request',
                related_id=None,
            )
        db.session.commit()
        flash("Ubutumwa bwo gusaba konti bwoherejwe ku Muyobozi.", 'success')
        return redirect(url_for('core.cashier_request_cash_account'))

    return render_template('cashier/request_cash_account.html')


@core_bp.route('/cashier/cash-transfer', methods=['GET', 'POST'])
@role_required('cashier')
def cashier_cash_transfer():
    accounts, recon_map = _cashier_accounts_context()
    if request.method == 'POST':
        try:
            from_account_id = int(request.form.get('from_account_id') or 0)
            to_account_id = int(request.form.get('to_account_id') or 0)
        except Exception:
            from_account_id = 0
            to_account_id = 0

        if not from_account_id or not to_account_id or from_account_id == to_account_id:
            flash("Hitamo konti ibiri zitandukanye (aho uyakuye n'aho uyashyize).", 'danger')
            return redirect(url_for('core.cashier_cash_transfer'))

        from_acc = CashAccount.query.get(from_account_id)
        to_acc = CashAccount.query.get(to_account_id)
        if not from_acc or not to_acc:
            flash("Konti wahisemo ntibashije kuboneka.", 'danger')
            return redirect(url_for('core.cashier_cash_transfer'))
        if (from_acc.currency or 'RWF').upper() != (to_acc.currency or 'RWF').upper():
            flash("Konti zombi zigomba kuba zifite ifaranga rimwe.", 'danger')
            return redirect(url_for('core.cashier_cash_transfer'))

        try:
            amount = float(request.form.get('amount') or 0.0)
        except Exception:
            amount = 0.0
        if amount <= 0:
            flash("Amafaranga agomba kuba arenga 0.", 'danger')
            return redirect(url_for('core.cashier_cash_transfer'))

        reference = (request.form.get('reference') or '').strip() or f"transfer:{from_acc.id}->{to_acc.id}"
        note = (request.form.get('note') or '').strip() or f"Transfer {from_acc.name} -> {to_acc.name}"

        payload = {
            'action': 'cash_transfer',
            'from_account_id': int(from_acc.id),
            'to_account_id': int(to_acc.id),
            'amount': float(amount),
            'currency': (from_acc.currency or 'RWF').upper(),
            'exchange_rate': 1.0,
            'amount_input': float(amount),
            'amount_rwf': float(amount) if (from_acc.currency or 'RWF').upper() == 'RWF' else None,
            'method': 'CASH',
            'reference': reference,
            'note': note,
        }
        review = PaymentReview(
            mineral_type=None,
            type='cash_transfer',
            customer=f"{from_acc.name} -> {to_acc.name}",
            amount=float(amount),
            currency=(from_acc.currency or 'RWF').upper(),
            created_by_id=getattr(current_user, 'id', None),
            status=PaymentReviewStatus.PENDING_REVIEW.value,
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.flush()

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='CASH_TRANSFER_REQUESTED',
                message=(
                    f"Umubitsi {getattr(current_user, 'username', 'unknown')} arasaba kwemezwa kohereza amafaranga: "
                    f"{from_acc.name} -> {to_acc.name}. Amount: {amount:,.2f} {(from_acc.currency or 'RWF').upper()}. "
                    f"Ref: {reference}."
                ),
                related_type='payment_review',
                related_id=int(review.id),
            )

        db.session.commit()
        flash("Ubusabe bwo kohereza amafaranga bwoherejwe ku Muyobozi.", 'success')
        return redirect(url_for('core.cashier_cash_transfer'))

    return render_template('cashier/cash_transfer.html', accounts=accounts, recon_map=recon_map)


@core_bp.route("/cashier/approved-requests", methods=["GET"])
@role_required("cashier", "boss", "admin")
def cashier_approved_requests():
    accounts, recon_map = _cashier_accounts_context()

    approved_waiting = (
        PaymentReview.query
        .filter(
            PaymentReview.status == PaymentReviewStatus.APPROVED.value,
            (PaymentReview.disbursement_status.is_(None)) | (PaymentReview.disbursement_status != 'DISBURSED'),
        )
        .order_by(PaymentReview.reviewed_at.desc(), PaymentReview.created_at.desc())
        .limit(200)
        .all()
    )
    approved_collections = []
    approved_payments = []
    for r in approved_waiting:
        p = {}
        try:
            p = json.loads(getattr(r, 'request_payload', None) or '{}') if getattr(r, 'request_payload', None) else {}
            if not isinstance(p, dict):
                p = {}
        except Exception:
            p = {}

        r.action = (p.get('action') or '').strip().lower() or None
        review_type = (getattr(r, 'type', None) or '').strip().lower()

        # Agreements are boss approvals, not cashier cash movements.
        if review_type == 'batch_agreement' or r.action == 'batch_agreement':
            continue

        if not r.action:
            if review_type == 'cash_transaction':
                r.action = 'cash_transaction'
            elif review_type == 'cash_collect_receipt':
                r.action = 'collect_receipt'
            elif review_type == 'cash_supplier_refund':
                r.action = 'supplier_refund'
            elif review_type == 'cash_collect_unearned_receipt':
                r.action = 'collect_unearned_receipt'
            elif review_type == 'loan_disbursement':
                r.action = 'loan_disbursement'
            elif review_type == 'loan_repayment':
                r.action = 'loan_repayment'
            elif review_type == 'cash_transfer':
                r.action = 'cash_transfer'

        r.method = (p.get('method') or 'CASH').upper()
        r.currency = (p.get('currency') or getattr(r, 'currency', None) or 'RWF').upper()
        r.direction = (p.get('direction') or '').strip().upper() or None
        r.requires_cash_account = bool(r.method == 'CASH')

        # Infer direction when it isn't explicitly stored in payload.
        if not r.direction:
            if r.action in {'collect_receipt', 'collect_unearned_receipt', 'supplier_refund', 'loan_disbursement'}:
                r.direction = 'IN'
            elif r.action in {'loan_repayment', 'cash_transfer'}:
                r.direction = 'OUT'
            elif review_type in {'cash_collect_receipt', 'cash_collect_unearned_receipt', 'cash_supplier_refund'}:
                r.direction = 'IN'
            else:
                # Supplier payments / internal expenses default to cash OUT.
                r.direction = 'OUT'

        currency, exchange_rate, amount_input, amount_rwf, _amount_cash = _money_from_payload(p, r)
        r.currency = currency
        if currency == 'USD':
            r.display_amount = f"{amount_input:,.2f} USD @ {exchange_rate:,.2f} = {amount_rwf:,.2f} RWF"
        else:
            r.display_amount = f"{amount_rwf:,.2f} {currency}"

        if r.direction == 'IN':
            approved_collections.append(r)
        else:
            approved_payments.append(r)

    return render_template(
        'cashier/approved_requests.html',
        accounts=accounts,
        recon_map=recon_map,
        approved_collections=approved_collections,
        approved_payments=approved_payments,
        cash_accounts=accounts,
    )


@core_bp.route('/api/cashier/approved-requests/summary', methods=['GET'])
@role_required('cashier', 'boss', 'admin')
def cashier_approved_requests_summary():
    """Small JSON payload for polling-based refresh."""
    approved_waiting = (
        PaymentReview.query
        .filter(
            PaymentReview.status == PaymentReviewStatus.APPROVED.value,
            (PaymentReview.disbursement_status.is_(None)) | (PaymentReview.disbursement_status != 'DISBURSED'),
        )
        .order_by(PaymentReview.id.desc())
        .limit(200)
        .all()
    )
    col = 0
    pay = 0
    max_id = 0
    for r in approved_waiting:
        try:
            max_id = max(max_id, int(getattr(r, 'id', 0) or 0))
        except Exception:
            pass
        payload = {}
        try:
            payload = json.loads(getattr(r, 'request_payload', None) or '{}') if getattr(r, 'request_payload', None) else {}
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        action = (payload.get('action') or '').strip().lower()
        review_type = (getattr(r, 'type', None) or '').strip().lower()

        if review_type == 'batch_agreement' or action == 'batch_agreement':
            continue

        if not action:
            if review_type == 'cash_transaction':
                action = 'cash_transaction'
            elif review_type == 'cash_collect_receipt':
                action = 'collect_receipt'
            elif review_type == 'cash_supplier_refund':
                action = 'supplier_refund'
            elif review_type == 'cash_collect_unearned_receipt':
                action = 'collect_unearned_receipt'
            elif review_type == 'loan_disbursement':
                action = 'loan_disbursement'
            elif review_type == 'loan_repayment':
                action = 'loan_repayment'
            elif review_type == 'cash_transfer':
                action = 'cash_transfer'

        direction = (payload.get('direction') or '').strip().upper() or None
        if not direction:
            if action in {'collect_receipt', 'collect_unearned_receipt', 'supplier_refund', 'loan_disbursement'}:
                direction = 'IN'
            elif action in {'loan_repayment', 'cash_transfer'}:
                direction = 'OUT'
            elif review_type in {'cash_collect_receipt', 'cash_collect_unearned_receipt', 'cash_supplier_refund'}:
                direction = 'IN'
            else:
                direction = 'OUT'

        if direction == 'IN':
            col += 1
        else:
            pay += 1

    return jsonify({'collections': col, 'payments': pay, 'max_id': max_id})


@core_bp.route("/cashier/pending-receipts", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_pending_receipts():
    accounts, recon_map = _cashier_accounts_context()

    if request.method == "POST":
        if getattr(current_user, 'role', None) != 'cashier':
            flash('Only cashier users can submit receipt collections.', 'warning')
            return redirect(url_for('core.cashier_pending_receipts'))

        receipt_kind = (request.form.get('receipt_kind') or 'earned').strip().lower()

        if receipt_kind == 'unearned':
            from core.models import CustomerUnearnedReceipt

            collect_id = int(request.form.get('collect_unearned_id') or 0)
            if not collect_id:
                flash('Missing unearned receipt.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))
            row = CustomerUnearnedReceipt.query.get(collect_id)
            if not row:
                flash('Unearned receipt not found.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))
            if (row.payment_channel or '').upper() != 'CASH':
                flash('This unearned receipt is not a cash receipt.', 'warning')
                return redirect(url_for('core.cashier_pending_receipts'))
            if row.is_collected:
                flash('Unearned receipt already collected.', 'info')
                return redirect(url_for('core.cashier_pending_receipts'))
            if not getattr(row, 'is_handed_over', False):
                flash('Uyu mwishyurize ntiwawohererezwa Umubitsi. Banza kohereza (handover) uvuye kuri Negotiator.', 'warning')
                return redirect(url_for('core.cashier_pending_receipts'))

            account_id = int(request.form.get('account_id') or 0)
            account = CashAccount.query.get(account_id)
            if not account:
                flash('Selected cash account not found.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))

            receipt_currency = (row.currency or 'RWF').upper()
            account_currency = (account.currency or 'RWF').upper()
            if receipt_currency == 'USD' and account_currency != 'USD':
                flash('This receipt is in USD. Select a USD cash account.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))
            if receipt_currency != 'USD' and account_currency == 'USD':
                flash('This receipt is in RWF. Select an RWF cash account.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))

            amt_cash = float(row.amount_input or 0.0) if receipt_currency == 'USD' else float(row.amount_rwf or 0.0)
            if amt_cash <= 0:
                flash('Invalid receipt amount.', 'danger')
                return redirect(url_for('core.cashier_pending_receipts'))

            payload = {
                'action': 'collect_unearned_receipt',
                'unearned_id': int(row.id),
                'account_id': int(account.id),
                'direction': 'IN',
                'amount': float(amt_cash),
                'currency': receipt_currency,
                'exchange_rate': float(row.exchange_rate or 1.0),
                'amount_input': float(row.amount_input or 0.0),
                'amount_rwf': float(row.amount_rwf or 0.0),
                'note': f"Collect unearned receipt #{row.id}",
                'method': 'CASH',
                'reference': f"unearned_receipt:{row.id}",
            }

            # Collect first (cash IN), then boss reviews.
            ref = str(payload.get('reference') or '').strip()

            existing_review = None
            if ref:
                existing_review = (
                    PaymentReview.query
                    .filter(
                        PaymentReview.type.in_(['cash_collect_unearned_receipt']),
                        PaymentReview.status.in_([
                            PaymentReviewStatus.PENDING_REVIEW.value,
                            PaymentReviewStatus.APPROVED.value,
                        ]),
                        (PaymentReview.disbursement_status.is_(None)) | (PaymentReview.disbursement_status != 'DISBURSED'),
                        PaymentReview.request_payload.ilike(f"%\"reference\": \"{ref}\"%"),
                    )
                    .order_by(PaymentReview.id.desc())
                    .first()
                )

            existing_tx = (
                CashTransaction.query
                .filter(CashTransaction.reference == ref)
                .order_by(CashTransaction.id.desc())
                .first()
            ) if ref else None

            if existing_tx:
                tx = existing_tx
            else:
                tx = CashTransaction(
                    account_id=account.id,
                    amount=float(amt_cash),
                    currency=receipt_currency,
                    exchange_rate=float(row.exchange_rate or 1.0),
                    amount_input=float(row.amount_input or 0.0),
                    amount_rwf=float(row.amount_rwf or 0.0),
                    direction='IN',
                    reference=ref,
                    note=f"Collected unearned receipt #{row.id}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) + float(amt_cash))
                db.session.add(tx)
                db.session.add(account)
                db.session.flush()

            row.is_collected = True
            row.collected_by_id = getattr(current_user, 'id', None)
            row.collected_at = datetime.utcnow()
            row.cash_account_id = int(account.id)
            db.session.add(row)

            if existing_review:
                review = existing_review
                review.request_payload = json.dumps(payload)
            else:
                review = PaymentReview(
                    mineral_type=None,
                    type='cash_collect_unearned_receipt',
                    customer=(row.customer or 'Customer'),
                    amount=float(payload.get('amount_input') or payload.get('amount') or amt_cash),
                    currency=receipt_currency,
                    created_by_id=getattr(current_user, 'id', None),
                    status=PaymentReviewStatus.PENDING_REVIEW.value,
                    request_payload=json.dumps(payload),
                )
                db.session.add(review)
                db.session.flush()

            review.disbursement_status = 'DISBURSED'
            review.disbursed_by_id = getattr(current_user, 'id', None)
            review.disbursed_at = datetime.utcnow()
            review.cash_transaction_id = int(tx.id)
            review.cash_account_id = int(account.id)

            boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=int(boss_id),
                    type_='UNEARNEED_RECEIPT_COLLECTED',
                    message=(
                        f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakiriye amafaranga y'umukiriya (unearned) {row.customer}. "
                        f"Amount: {amt_cash:,.2f} {receipt_currency} (rate {float(row.exchange_rate or 1.0):,.2f}, {float(row.amount_rwf or 0.0):,.2f} RWF)."
                    ),
                    related_type='payment_review',
                    related_id=int(review.id),
                )

            db.session.commit()
            flash('Unearned receipt collected. Boss has been notified for review.', 'success')
            return redirect(url_for('core.cashier_pending_receipts'))

        collect_receipt_id = int(request.form.get('collect_receipt_id') or 0)
        if not collect_receipt_id:
            flash('Missing receipt.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))

        receipt = CustomerReceipt.query.get(collect_receipt_id)
        if not receipt:
            flash('Receipt not found.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))
        if (receipt.payment_channel or '').upper() != 'CASH':
            flash('This receipt is not a cash receipt.', 'warning')
            return redirect(url_for('core.cashier_pending_receipts'))
        if receipt.is_collected:
            flash('Receipt already collected.', 'info')
            return redirect(url_for('core.cashier_pending_receipts'))
        if not getattr(receipt, 'is_handed_over', False):
            flash('Uyu mwishyurize ntiwawohererezwa Umubitsi. Banza kohereza (handover) uvuye kuri Negotiator.', 'warning')
            return redirect(url_for('core.cashier_pending_receipts'))

        account_id = int(request.form.get('account_id') or 0)
        account = CashAccount.query.get(account_id)
        if not account:
            flash('Selected cash account not found.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))

        receipt_currency = (receipt.currency or 'RWF').upper()
        account_currency = (account.currency or 'RWF').upper()
        if receipt_currency == 'USD' and account_currency != 'USD':
            flash('This receipt is in USD. Select a USD cash account.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))
        if receipt_currency != 'USD' and account_currency == 'USD':
            flash('This receipt is in RWF. Select an RWF cash account.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))

        amt_cash = float(receipt.amount_input or 0.0) if receipt_currency == 'USD' else float(receipt.amount_rwf or 0.0)
        if amt_cash <= 0:
            flash('Invalid receipt amount.', 'danger')
            return redirect(url_for('core.cashier_pending_receipts'))

        payload = {
            'action': 'collect_receipt',
            'receipt_id': int(receipt.id),
            'account_id': int(account.id),
            'direction': 'IN',
            'amount': float(amt_cash),
            'currency': receipt_currency,
            'exchange_rate': float(receipt.exchange_rate or 1.0),
            'amount_input': float(receipt.amount_input or 0.0),
            'amount_rwf': float(receipt.amount_rwf or 0.0),
            'note': f"Collect receipt #{receipt.id}",
            'method': 'CASH',
            'reference': f"receipt:{receipt.id}",
        }

        # Collect first (cash IN), then boss reviews.
        ref = str(payload.get('reference') or '').strip()

        existing_review = None
        if ref:
            existing_review = (
                PaymentReview.query
                .filter(
                    PaymentReview.type.in_(['cash_collect_receipt']),
                    PaymentReview.status.in_([
                        PaymentReviewStatus.PENDING_REVIEW.value,
                        PaymentReviewStatus.APPROVED.value,
                    ]),
                    (PaymentReview.disbursement_status.is_(None)) | (PaymentReview.disbursement_status != 'DISBURSED'),
                    PaymentReview.request_payload.ilike(f"%\"reference\": \"{ref}\"%"),
                )
                .order_by(PaymentReview.id.desc())
                .first()
            )

        existing_tx = (
            CashTransaction.query
            .filter(CashTransaction.reference == ref)
            .order_by(CashTransaction.id.desc())
            .first()
        ) if ref else None

        if existing_tx:
            tx = existing_tx
        else:
            tx = CashTransaction(
                account_id=account.id,
                amount=float(amt_cash),
                currency=receipt_currency,
                exchange_rate=float(receipt.exchange_rate or 1.0),
                amount_input=float(receipt.amount_input or 0.0),
                amount_rwf=float(receipt.amount_rwf or 0.0),
                direction='IN',
                reference=ref,
                note=f"Collected receipt #{receipt.id}",
                created_by_id=getattr(current_user, 'id', None),
            )
            account.current_balance = float((account.current_balance or 0.0) + float(amt_cash))
            db.session.add(tx)
            db.session.add(account)
            db.session.flush()

        receipt.is_collected = True
        receipt.collected_by_id = getattr(current_user, 'id', None)
        receipt.collected_at = datetime.utcnow()
        receipt.cash_account_id = int(account.id)
        db.session.add(receipt)

        if existing_review:
            review = existing_review
            review.request_payload = json.dumps(payload)
        else:
            review = PaymentReview(
                mineral_type=None,
                type='cash_collect_receipt',
                customer=(receipt.customer or getattr(receipt.plan, 'customer', None) or 'Customer'),
                amount=float(payload.get('amount_input') or payload.get('amount') or amt_cash),
                currency=receipt_currency,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=json.dumps(payload),
            )
            db.session.add(review)
            db.session.flush()

        review.disbursement_status = 'DISBURSED'
        review.disbursed_by_id = getattr(current_user, 'id', None)
        review.disbursed_at = datetime.utcnow()
        review.cash_transaction_id = int(tx.id)
        review.cash_account_id = int(account.id)

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='RECEIPT_COLLECTED',
                message=(
                    f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakiriye amafaranga y'umukiriya {receipt.customer}. "
                    f"Amount: {amt_cash:,.2f} {receipt_currency} (rate {float(receipt.exchange_rate or 1.0):,.2f}, {float(receipt.amount_rwf or 0.0):,.2f} RWF)."
                ),
                related_type='payment_review',
                related_id=int(review.id),
            )

        db.session.commit()
        flash('Receipt collected. Boss has been notified for review.', 'success')
        return redirect(url_for('core.cashier_pending_receipts'))

    pending_receipts = (
        CustomerReceipt.query
        .filter(
            CustomerReceipt.payment_channel == 'CASH',
            CustomerReceipt.is_collected == False,
            CustomerReceipt.is_handed_over == True,
        )
        .order_by(CustomerReceipt.created_at.asc())
        .limit(200)
        .all()
    )

    try:
        from core.models import CustomerUnearnedReceipt
        pending_unearned = (
            CustomerUnearnedReceipt.query
            .filter(
                CustomerUnearnedReceipt.payment_channel == 'CASH',
                CustomerUnearnedReceipt.is_collected == False,
                CustomerUnearnedReceipt.is_handed_over == True,
            )
            .order_by(CustomerUnearnedReceipt.received_at.asc())
            .limit(200)
            .all()
        )
    except Exception:
        pending_unearned = []
    return render_template(
        'cashier/pending_receipts.html',
        accounts=accounts,
        recon_map=recon_map,
        pending_receipts=pending_receipts,
        pending_unearned=pending_unearned,
        cash_accounts=accounts,
    )


@core_bp.route('/api/cashier/pending-receipts/summary', methods=['GET'])
@role_required('cashier', 'boss', 'admin')
def cashier_pending_receipts_summary():
    try:
        from core.models import CustomerUnearnedReceipt
    except Exception:
        CustomerUnearnedReceipt = None

    pending_receipts_count = (
        CustomerReceipt.query
        .filter(
            CustomerReceipt.payment_channel == 'CASH',
            CustomerReceipt.is_collected.is_(False),
            CustomerReceipt.is_handed_over.is_(True),
        )
        .count()
    )
    pending_unearned_count = 0
    if CustomerUnearnedReceipt is not None:
        try:
            pending_unearned_count = (
                CustomerUnearnedReceipt.query
                .filter(
                    CustomerUnearnedReceipt.payment_channel == 'CASH',
                    CustomerUnearnedReceipt.is_collected.is_(False),
                    CustomerUnearnedReceipt.is_handed_over.is_(True),
                )
                .count()
            )
        except Exception:
            pending_unearned_count = 0

    return jsonify({'earned': int(pending_receipts_count or 0), 'unearned': int(pending_unearned_count or 0)})


@core_bp.route("/cashier/manual-cash", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_manual_cash():
    accounts, recon_map = _cashier_accounts_context()

    if request.method == "POST":
        if getattr(current_user, 'role', None) != 'cashier':
            flash('Only cashier users can submit cash movements.', 'warning')
            return redirect(url_for('core.cashier_manual_cash'))

        account_id = int(request.form.get('account_id') or 0)
        amount = float(request.form.get('amount') or 0)
        direction = (request.form.get('direction') or 'IN').upper()
        note = request.form.get('note') or None

        account = CashAccount.query.get(account_id)
        if not account:
            flash('Selected cash account not found.', 'danger')
            return redirect(url_for('core.cashier_manual_cash'))
        if amount <= 0:
            flash('Amount must be greater than zero.', 'danger')
            return redirect(url_for('core.cashier_manual_cash'))

        payload = {
            'action': 'cash_transaction',
            'account_id': int(account.id),
            'direction': direction,
            'amount': float(amount),
            'currency': (account.currency or 'RWF').upper(),
            'exchange_rate': 1.0,
            'amount_input': float(amount),
            'amount_rwf': float(amount) if (account.currency or 'RWF').upper() == 'RWF' else None,
            'note': note,
            'method': 'CASH',
            'reference': f"cash_request:{account.id}:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        }
        review = PaymentReview(
            mineral_type=None,
            type='cash_transaction',
            customer=account.name,
            amount=float(amount),
            currency=(account.currency or 'RWF').upper(),
            created_by_id=getattr(current_user, 'id', None),
            status=PaymentReviewStatus.PENDING_REVIEW.value,
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.commit()
        flash('Cash movement submitted for boss approval.', 'success')
        return redirect(url_for('core.cashier_manual_cash'))

    return render_template('cashier/manual_cash.html', accounts=accounts, recon_map=recon_map)


@core_bp.route("/cashier/daily-closing", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_daily_closing():
    accounts, recon_map = _cashier_accounts_context()

    reconciliations = []
    try:
        from core.models import CashReconciliation
        reconciliations = (
            CashReconciliation.query
            .filter(CashReconciliation.is_deleted.is_(False))
            .order_by(CashReconciliation.recon_date.desc(), CashReconciliation.created_at.desc())
            .limit(200)
            .all()
        )
    except Exception:
        reconciliations = []

    if request.method == "POST":
        if getattr(current_user, 'role', None) != 'cashier':
            flash('Only cashier users can save closings.', 'warning')
            return redirect(url_for('core.cashier_daily_closing'))

        try:
            reconcile_account_id = int(request.form.get('reconcile_account_id') or 0)
        except Exception:
            reconcile_account_id = 0

        reconcile_date_raw = (request.form.get('reconcile_date') or '').strip()
        reconcile_counted_raw = (request.form.get('reconcile_counted_balance') or '').strip()
        note = (request.form.get('reconcile_note') or '').strip() or None

        if not (reconcile_account_id and reconcile_date_raw and reconcile_counted_raw):
            flash('All fields are required.', 'danger')
            return redirect(url_for('core.cashier_daily_closing'))

        from core.models import CashReconciliation
        try:
            recon_date = datetime.strptime(reconcile_date_raw, '%Y-%m-%d').date()
        except Exception:
            flash('Invalid reconciliation date.', 'danger')
            return redirect(url_for('core.cashier_daily_closing'))

        try:
            counted_balance = float(reconcile_counted_raw)
        except Exception:
            counted_balance = -1

        if counted_balance < 0:
            flash('Counted balance must be a valid number >= 0.', 'danger')
            return redirect(url_for('core.cashier_daily_closing'))

        account = CashAccount.query.get(reconcile_account_id)
        if not account:
            flash('Selected cash account not found.', 'danger')
            return redirect(url_for('core.cashier_daily_closing'))

        existing = (
            CashReconciliation.query
            .filter(
                CashReconciliation.is_deleted.is_(False),
                CashReconciliation.account_id == account.id,
                CashReconciliation.recon_date == recon_date,
            )
            .first()
        )
        if existing:
            flash('This account is already reconciled for the selected date.', 'warning')
            return redirect(url_for('core.cashier_daily_closing'))

        expected_balance = float(account.current_balance or 0.0)
        variance = float(counted_balance - expected_balance)
        rec = CashReconciliation(
            account_id=account.id,
            recon_date=recon_date,
            expected_balance=expected_balance,
            counted_balance=counted_balance,
            variance=variance,
            note=note,
            created_by_id=getattr(current_user, 'id', None),
        )
        db.session.add(rec)
        db.session.commit()
        flash(f"Reconciliation saved for {account.name} ({recon_date}). Variance: {variance:,.2f}.", 'success')
        return redirect(url_for('core.cashier_daily_closing'))

    return render_template(
        'cashier/daily_closing.html',
        accounts=accounts,
        recon_map=recon_map,
        reconciliations=reconciliations,
    )


@core_bp.route("/cashier/supplier-refund", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_supplier_refund():
    accounts, recon_map = _cashier_accounts_context()

    if request.method == "POST":
        if getattr(current_user, 'role', None) != 'cashier':
            flash('Only cashier users can submit supplier refunds.', 'warning')
            return redirect(url_for('core.cashier_supplier_refund'))

        from core.models import UnifiedSupplierAdvance

        refund_supplier_name = (request.form.get('refund_supplier_name') or '').strip()
        refund_supplier_norm = (request.form.get('refund_supplier_norm') or '').strip()
        refund_amount = float(request.form.get('refund_amount') or 0)
        refund_account_id = int(request.form.get('refund_account_id') or 0)
        if not (refund_supplier_name and refund_amount > 0 and refund_account_id):
            flash('All fields are required.', 'danger')
            return redirect(url_for('core.cashier_supplier_refund'))

        supplier_norm = refund_supplier_norm or ' '.join(refund_supplier_name.lower().split())
        supplier_norm = ' '.join((supplier_norm or '').strip().lower().split())
        if not supplier_norm:
            flash('Supplier is required.', 'danger')
            return redirect(url_for('core.cashier_supplier_refund'))

        # Resolve a canonical supplier_name from existing unified advance rows.
        canonical_name = None
        try:
            canonical_name = (
                db.session.query(func.max(UnifiedSupplierAdvance.supplier_name))
                .filter(
                    UnifiedSupplierAdvance.is_deleted.is_(False),
                    UnifiedSupplierAdvance.supplier_name_norm == supplier_norm,
                )
                .scalar()
            )
        except Exception:
            canonical_name = None

        if canonical_name:
            refund_supplier_name = str(canonical_name).strip() or refund_supplier_name

        reference = f"supplier_refund_request:{supplier_norm}"
        dup = (
            PaymentReview.query
            .filter(
                PaymentReview.status.in_([PaymentReviewStatus.PENDING_REVIEW.value, PaymentReviewStatus.APPROVED.value]),
                PaymentReview.disbursement_status == 'NOT_DISBURSED',
                PaymentReview.type == 'cash_supplier_refund',
                PaymentReview.request_payload.contains(reference),
            )
            .first()
        )
        if dup:
            flash('There is already a pending/approved supplier refund request for this supplier.', 'warning')
            return redirect(url_for('core.cashier_supplier_refund'))

        account = CashAccount.query.get(refund_account_id)
        if not account:
            flash('Selected cash account not found.', 'danger')
            return redirect(url_for('core.cashier_supplier_refund'))

        payload = {
            'action': 'supplier_refund',
            'supplier_name': refund_supplier_name,
            'supplier_norm': supplier_norm,
            'account_id': int(account.id),
            'direction': 'IN',
            'amount': float(refund_amount),
            'currency': (account.currency or 'RWF').upper(),
            'exchange_rate': 1.0,
            'amount_input': float(refund_amount),
            'amount_rwf': float(refund_amount) if (account.currency or 'RWF').upper() == 'RWF' else None,
            'note': f"Supplier refund - {refund_supplier_name}",
            'method': 'CASH',
            'reference': reference,
        }
        review = PaymentReview(
            mineral_type=None,
            type='cash_supplier_refund',
            customer=refund_supplier_name,
            amount=float(refund_amount),
            currency=(account.currency or 'RWF').upper(),
            created_by_id=getattr(current_user, 'id', None),
            status=PaymentReviewStatus.PENDING_REVIEW.value,
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.commit()
        flash('Supplier refund submitted for boss approval.', 'success')
        return redirect(url_for('core.cashier_supplier_refund'))

    return render_template('cashier/supplier_refund.html', accounts=accounts, recon_map=recon_map)


@core_bp.route('/api/suppliers/autocomplete')
@role_required('cashier', 'boss', 'admin', 'accountant')
def suppliers_autocomplete():
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'results': []})

    q_norm = ' '.join(q.lower().split())
    try:
        from core.models import UnifiedSupplierAdvance
        rows = (
            db.session.query(UnifiedSupplierAdvance.supplier_name)
            .filter(
                UnifiedSupplierAdvance.is_deleted.is_(False),
                UnifiedSupplierAdvance.supplier_name_norm.contains(q_norm),
            )
            .distinct()
            .order_by(UnifiedSupplierAdvance.supplier_name.asc())
            .limit(15)
            .all()
        )
        results = [nm for (nm,) in rows if nm]
        return jsonify({'results': results})
    except Exception:
        return jsonify({'results': []})


@core_bp.route("/cashier/transactions", methods=["GET"])
@role_required("cashier", "boss", "admin")
def cashier_transactions():
    accounts, recon_map = _cashier_accounts_context()
    recent = CashTransaction.query.order_by(CashTransaction.created_at.desc()).limit(200).all()
    return render_template('cashier/transactions.html', accounts=accounts, recon_map=recon_map, recent=recent)


@core_bp.route("/cashier/payment_review/<int:review_id>/disburse", methods=["POST"])
@role_required("cashier", "boss", "admin")
def cashier_disburse_payment_review(review_id: int):
    if getattr(current_user, 'role', None) != 'cashier':
        flash('Only cashier users can disburse approved requests.', 'warning')
        return redirect(url_for('core.cashier_dashboard'))

    review = PaymentReview.query.get_or_404(review_id)
    if review.status != PaymentReviewStatus.APPROVED.value:
        flash('Only APPROVED requests can be disbursed.', 'warning')
        return redirect(url_for('core.cashier_dashboard'))
    if (review.disbursement_status or 'NOT_DISBURSED') == 'DISBURSED':
        flash('This request is already disbursed.', 'info')
        return redirect(url_for('core.cashier_dashboard'))

    payload = {}
    try:
        payload = json.loads(review.request_payload or '{}') if review.request_payload else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    review_type = (review.type or '').strip().lower()
    mineral = (review.mineral_type or '').strip().lower()
    action = (payload.get('action') or '').strip().lower()
    if not action:
        if review_type == 'cash_transaction':
            action = 'cash_transaction'
        elif review_type == 'cash_collect_receipt':
            action = 'collect_receipt'
        elif review_type == 'cash_supplier_refund':
            action = 'supplier_refund'
        elif review_type == 'cash_collect_unearned_receipt':
            action = 'collect_unearned_receipt'
        elif review_type == 'loan_disbursement':
            action = 'loan_disbursement'
        elif review_type == 'loan_repayment':
            action = 'loan_repayment'
        elif review_type == 'cash_transfer':
            action = 'cash_transfer'

    try:
        # Execute per approved request.
        currency, exchange_rate, amount_input, amount_rwf, amount_cash = _money_from_payload(payload, review)
        method = (payload.get('method') or 'CASH').upper()
        reference = payload.get('reference') or f"review-{review.id}"
        note = payload.get('note') or review.boss_comment

        try:
            chosen_account_id = int(request.form.get('cash_account_id') or 0)
        except Exception:
            chosen_account_id = 0

        # Cash desk actions (cash account required)
        if action in {'cash_transaction', 'collect_receipt', 'collect_unearned_receipt', 'supplier_refund', 'loan_disbursement', 'loan_repayment', 'cash_transfer'}:
            if action == 'cash_transfer':
                try:
                    account_id = int(payload.get('from_account_id') or 0)
                except Exception:
                    account_id = 0
            else:
                try:
                    account_id = int(payload.get('account_id') or 0)
                except Exception:
                    account_id = 0
                if chosen_account_id:
                    account_id = chosen_account_id

            if not account_id:
                flash('Cash account is required to disburse this request.', 'danger')
                return redirect(url_for('core.cashier_dashboard'))
            account = CashAccount.query.get(account_id)
            if not account:
                flash('Selected cash account not found.', 'danger')
                return redirect(url_for('core.cashier_dashboard'))

            direction = (payload.get('direction') or 'IN').strip().upper()
            tx_amount = float(amount_cash or 0.0)
            if tx_amount <= 0:
                raise ValueError('Amount must be > 0.')

            account_currency = (account.currency or 'RWF').strip().upper()
            # Enforce that physical USD cash moves (IN/OUT) use a USD cash account.
            if (currency or 'RWF').upper() == 'USD' and account_currency != 'USD':
                raise ValueError(
                    "Uyu mwishyurize uri muri USD. Ugomba guhitamo konti ya USD. Niba itabaho, saba Boss kuyishyiraho."
                )
            if (currency or 'RWF').upper() != 'USD' and account_currency == 'USD':
                raise ValueError('Uyu mwishyurize uri muri RWF. Hitamo konti ya RWF.')

            if action == 'loan_disbursement':
                from core.models import Loan, LoanLedgerEntry

                try:
                    loan_id = int(payload.get('loan_id') or 0)
                except Exception:
                    loan_id = 0
                if not loan_id:
                    raise ValueError('Missing loan_id.')
                loan = Loan.query.get(loan_id)
                if not loan:
                    raise ValueError('Loan not found.')
                if (loan.status or '').upper() not in {'APPROVED'}:
                    raise ValueError('Loan is not approved for disbursement.')

                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=currency,
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or 0.0),
                    direction='IN',
                    reference=f"loan:{int(loan.id)}",
                    note=note or f"Loan disbursement - {loan.lender_name}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) + tx_amount)
                db.session.add(tx)
                db.session.add(account)
                db.session.flush()

                entry = LoanLedgerEntry(
                    loan_id=int(loan.id),
                    entry_type='DISBURSEMENT',
                    amount_input=float(amount_input or tx_amount),
                    currency=currency,
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_rwf=float(amount_rwf or 0.0),
                    cash_account_id=int(account.id),
                    cash_transaction_id=int(tx.id),
                    created_by_id=getattr(current_user, 'id', None),
                    created_at=datetime.utcnow(),
                    note=note,
                )
                loan.disbursed_rwf = float((loan.disbursed_rwf or 0.0) + float(amount_rwf or 0.0))
                loan.status = 'DISBURSED'
                db.session.add(entry)
                db.session.add(loan)
                db.session.flush()

                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='LOAN_DISBURSED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yatanze inguzanyo ya {loan.lender_name} "
                            f"kuri konti {account.name}. Amount: {tx_amount:,.2f} {currency} (={float(amount_rwf or 0.0):,.2f} RWF)."
                        ),
                        related_type='loan',
                        related_id=int(loan.id),
                    )

            elif action == 'loan_repayment':
                from core.models import Loan, LoanLedgerEntry

                if (currency or 'RWF').upper() != 'RWF':
                    raise ValueError('Loan repayments must be disbursed in RWF (loan ledger is in RWF).')

                lender_name = (payload.get('lender_name') or review.customer or '').strip()
                lender_norm = ' '.join((payload.get('lender_name_norm') or lender_name).strip().lower().split())
                if not lender_name or not lender_norm:
                    raise ValueError('Lender is required for repayment.')

                loans = (
                    Loan.query
                    .filter(
                        Loan.lender_name_norm == lender_norm,
                        Loan.outstanding_rwf > 0,
                    )
                    .with_for_update()
                    .order_by(Loan.created_at.asc(), Loan.id.asc())
                    .all()
                )
                if not loans:
                    raise ValueError('No outstanding loans found for this lender.')

                total_outstanding = float(sum([float(l.outstanding_rwf or 0.0) for l in loans]) or 0.0)
                if tx_amount > total_outstanding:
                    raise ValueError(f"Repayment exceeds outstanding balance ({total_outstanding:,.2f} RWF).")

                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=(account.currency or 'RWF').upper(),
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='OUT',
                    reference=f"lender_repayment:{lender_norm}",
                    note=note or f"Lender repayment - {lender_name}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) - tx_amount)
                if account.current_balance < 0:
                    raise ValueError('Insufficient funds in selected cash account.')

                db.session.add(tx)
                db.session.add(account)
                db.session.flush()

                remaining = float(tx_amount)
                for loan in loans:
                    if remaining <= 0:
                        break
                    can_apply = min(float(loan.outstanding_rwf or 0.0), remaining)
                    if can_apply <= 0:
                        continue
                    loan.outstanding_rwf = float((loan.outstanding_rwf or 0.0) - can_apply)
                    loan.repaid_rwf = float((loan.repaid_rwf or 0.0) + can_apply)
                    db.session.add(loan)
                    db.session.flush()

                    entry = LoanLedgerEntry(
                        loan_id=int(loan.id),
                        entry_type='REPAYMENT',
                        amount_input=float(amount_input or tx_amount),
                        currency='RWF',
                        exchange_rate=1.0,
                        amount_rwf=float(can_apply),
                        cash_account_id=int(account.id),
                        cash_transaction_id=int(tx.id),
                        created_by_id=getattr(current_user, 'id', None),
                        note=f"{(payload.get('method') or 'CASH').upper()} repayment for {lender_name}",
                    )
                    db.session.add(entry)
                    remaining = float(remaining - can_apply)

                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='LENDER_REPAID',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yishyuye uwatanze inguzanyo {lender_name} "
                            f"kuri konti {account.name}. Amount: {tx_amount:,.2f} RWF."
                        ),
                        related_type='cash_transaction',
                        related_id=int(tx.id),
                    )

            elif action == 'cash_transfer':
                try:
                    from_account_id = int(payload.get('from_account_id') or 0)
                    to_account_id = int(payload.get('to_account_id') or 0)
                except Exception:
                    from_account_id = 0
                    to_account_id = 0
                if not from_account_id or not to_account_id or from_account_id == to_account_id:
                    raise ValueError('Invalid transfer accounts.')

                # Lock both accounts to keep balances consistent.
                acc_rows = (
                    CashAccount.query
                    .filter(CashAccount.id.in_([from_account_id, to_account_id]))
                    .with_for_update()
                    .all()
                )
                acc_map = {int(a.id): a for a in (acc_rows or [])}
                from_acc = acc_map.get(int(from_account_id))
                to_acc = acc_map.get(int(to_account_id))
                if not from_acc or not to_acc:
                    raise ValueError('Transfer accounts not found.')
                if (from_acc.currency or 'RWF').upper() != (to_acc.currency or 'RWF').upper():
                    raise ValueError('Transfer accounts must have the same currency.')
                if float(from_acc.current_balance or 0.0) < float(tx_amount):
                    raise ValueError('Insufficient funds in source account for transfer.')

                # OUT from source
                out_tx = CashTransaction(
                    account_id=from_acc.id,
                    amount=tx_amount,
                    currency=(from_acc.currency or 'RWF').upper(),
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='OUT',
                    reference=reference,
                    note=note or f"Transfer OUT to {to_acc.name}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                from_acc.current_balance = float((from_acc.current_balance or 0.0) - tx_amount)
                db.session.add(out_tx)
                db.session.add(from_acc)
                db.session.flush()

                # IN to destination
                in_tx = CashTransaction(
                    account_id=to_acc.id,
                    amount=tx_amount,
                    currency=(to_acc.currency or 'RWF').upper(),
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='IN',
                    reference=reference,
                    note=note or f"Transfer IN from {from_acc.name}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                to_acc.current_balance = float((to_acc.current_balance or 0.0) + tx_amount)
                db.session.add(in_tx)
                db.session.add(to_acc)
                db.session.flush()

                # Link review to the OUT tx (audit anchor)
                review.cash_transaction_id = int(out_tx.id)
                review.cash_account_id = int(from_acc.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='CASH_TRANSFER_EXECUTED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yimuriye amafaranga kuri konti: "
                            f"{from_acc.name} -> {to_acc.name}. Amount: {tx_amount:,.2f} {(from_acc.currency or 'RWF').upper()}. "
                            f"Ref: {reference}."
                        ),
                        related_type='payment_review',
                        related_id=int(review.id),
                    )

            elif action == 'collect_unearned_receipt':
                from core.models import CustomerUnearnedReceipt

                try:
                    unearned_id = int(payload.get('unearned_id') or 0)
                except Exception:
                    unearned_id = 0
                if not unearned_id:
                    raise ValueError('Missing unearned receipt for collection.')
                row = CustomerUnearnedReceipt.query.get(unearned_id)
                if not row:
                    raise ValueError('Unearned receipt not found.')
                if row.is_collected:
                    existing_tx = CashTransaction.query.filter(CashTransaction.reference == f"unearned_receipt:{int(row.id)}").order_by(CashTransaction.id.desc()).first()
                    review.disbursement_status = 'DISBURSED'
                    review.disbursed_by_id = getattr(current_user, 'id', None)
                    review.disbursed_at = datetime.utcnow()
                    if existing_tx:
                        review.cash_transaction_id = int(existing_tx.id)
                        review.cash_account_id = int(existing_tx.account_id)
                    db.session.add(review)
                    db.session.commit()
                    flash('This unearned receipt was already collected; request has been closed.', 'info')
                    return redirect(url_for('core.cashier_pending_receipts'))
                if (row.payment_channel or '').upper() != 'CASH':
                    raise ValueError('Only CASH unearned receipts can be collected.')

                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=(account.currency or 'RWF').upper(),
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or 0.0),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='IN',
                    reference=f"unearned_receipt:{int(row.id)}",
                    note=note or f"Collect unearned receipt #{row.id}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) + tx_amount)
                row.is_collected = True
                row.collected_by_id = getattr(current_user, 'id', None)
                row.collected_at = datetime.utcnow()
                row.cash_account_id = account.id
                db.session.add(tx)
                db.session.add(account)
                db.session.add(row)
                db.session.flush()
                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='UNEARNED_RECEIPT_COLLECTED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakiriye amafaranga y'unearned receipt #{row.id} "
                            f"akayiashyira kuri konti {account.name}. Amount: {tx_amount:,.2f} {currency}" + (f" (={float(amount_rwf or 0.0):,.2f} RWF)" if currency == 'USD' else "") + "."
                        ),
                        related_type='customer_unearned_receipt',
                        related_id=int(row.id),
                    )

            elif action == 'collect_receipt':
                try:
                    receipt_id = int(payload.get('receipt_id') or 0)
                except Exception:
                    receipt_id = 0
                if not receipt_id:
                    raise ValueError('Missing receipt for collection.')
                receipt = CustomerReceipt.query.get(receipt_id)
                if not receipt:
                    raise ValueError('Receipt not found.')
                if receipt.is_collected:
                    existing_tx = CashTransaction.query.filter(CashTransaction.reference == f"receipt:{int(receipt.id)}").order_by(CashTransaction.id.desc()).first()
                    review.disbursement_status = 'DISBURSED'
                    review.disbursed_by_id = getattr(current_user, 'id', None)
                    review.disbursed_at = datetime.utcnow()
                    if existing_tx:
                        review.cash_transaction_id = int(existing_tx.id)
                        review.cash_account_id = int(existing_tx.account_id)
                    db.session.add(review)
                    db.session.commit()
                    flash('This receipt was already collected; request has been closed.', 'info')
                    return redirect(url_for('core.cashier_pending_receipts'))
                if (receipt.payment_channel or '').upper() != 'CASH':
                    raise ValueError('Only CASH receipts can be collected.')

                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=(account.currency or 'RWF').upper(),
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or 0.0),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='IN',
                    reference=f"receipt:{int(receipt.id)}",
                    note=note or f"Collect receipt #{receipt.id}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) + tx_amount)
                receipt.is_collected = True
                receipt.collected_by_id = getattr(current_user, 'id', None)
                receipt.collected_at = datetime.utcnow()
                receipt.cash_account_id = account.id
                db.session.add(tx)
                db.session.add(account)
                db.session.add(receipt)
                db.session.flush()
                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                # Notify bosses about the collection (money physically moved).
                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='RECEIPT_COLLECTED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakiriye amafaranga ya receipt #{receipt.id} "
                            f"akayiashyira kuri konti {account.name}. Amount: {tx_amount:,.2f} {currency}" + (f" (={float(amount_rwf or 0.0):,.2f} RWF)" if currency == 'USD' else "") + "."
                        ),
                        related_type='customer_receipt',
                        related_id=int(receipt.id),
                    )

            elif action == 'supplier_refund':
                from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation

                supplier_norm_payload = (payload.get('supplier_norm') or '').strip()
                supplier_name = (payload.get('supplier_name') or review.customer or '').strip()
                if not supplier_name:
                    raise ValueError('Supplier name is required for refund.')

                def _norm(nm: str) -> str:
                    return ' '.join((nm or '').strip().lower().split())

                supplier_norm = _norm(supplier_norm_payload or supplier_name)

                if (currency or 'RWF').upper() != 'RWF':
                    raise ValueError('Supplier refunds must be disbursed in RWF (advance ledger is in RWF).')
                advances = (
                    UnifiedSupplierAdvance.query
                    .filter(
                        UnifiedSupplierAdvance.is_deleted.is_(False),
                        UnifiedSupplierAdvance.supplier_name_norm == supplier_norm,
                        UnifiedSupplierAdvance.advance_remaining > 0,
                    )
                    .with_for_update()
                    .order_by(UnifiedSupplierAdvance.paid_at.asc(), UnifiedSupplierAdvance.id.asc())
                    .all()
                )
                if not advances:
                    raise ValueError('Supplier not found in advance ledger (use autocomplete and pick the correct supplier).')
                total_wallet = float(sum([float(a.advance_remaining or 0.0) for a in advances]) or 0.0)
                if total_wallet <= 0:
                    raise ValueError('This supplier has no remaining advance balance to refund.')
                if tx_amount > total_wallet:
                    raise ValueError(f"Refund amount exceeds supplier advance remaining ({total_wallet:,.2f} RWF).")

                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=currency,
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction='IN',
                    reference=f"supplier_refund:{supplier_norm}",
                    note=note or f"Supplier refund - {supplier_name}",
                    created_by_id=getattr(current_user, 'id', None),
                )
                account.current_balance = float((account.current_balance or 0.0) + tx_amount)
                db.session.add(tx)
                db.session.add(account)
                db.session.flush()

                refund_row = UnifiedSupplierAdvance(
                    supplier_name=supplier_name,
                    supplier_name_norm=supplier_norm,
                    source_mineral_type='refund',
                    source_payment_id=None,
                    input_amount=float(-tx_amount),
                    currency='RWF',
                    exchange_rate=1.0,
                    amount_rwf=float(-tx_amount),
                    paid_at=datetime.utcnow(),
                    method='CASH',
                    reference=f"cash_tx:{getattr(tx, 'id', None)}",
                    note=f"Supplier refund posted to cash account {account.name}",
                    advance_remaining=0.0,
                    created_by_id=getattr(current_user, 'id', None),
                )
                db.session.add(refund_row)
                db.session.flush()

                remaining = float(tx_amount)
                for adv in advances:
                    if remaining <= 0:
                        break
                    available = float(adv.advance_remaining or 0.0)
                    if available <= 0:
                        continue
                    apply_amt = min(available, remaining)
                    if apply_amt <= 0:
                        continue
                    adv.advance_remaining = max(available - apply_amt, 0.0)
                    remaining = max(remaining - apply_amt, 0.0)
                    db.session.add(adv)
                    db.session.add(UnifiedSupplierAdvanceAllocation(
                        advance_id=adv.id,
                        stock_mineral_type='refund',
                        stock_id=int(refund_row.id),
                        applied_amount=float(apply_amt),
                    ))

                if remaining > 0:
                    raise ValueError('Refund could not be applied to supplier advances (try again).')

                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='SUPPLIER_REFUND_COLLECTED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yakiriye supplier refund ya {supplier_name} "
                            f"akayiashyira kuri konti {account.name}. Amount: {tx_amount:,.2f} {currency}."
                        ),
                        related_type='payment_review',
                        related_id=int(review.id),
                    )

            else:
                # cash_transaction
                tx = CashTransaction(
                    account_id=account.id,
                    amount=tx_amount,
                    currency=currency,
                    exchange_rate=float(exchange_rate or 1.0),
                    amount_input=float(amount_input or tx_amount),
                    amount_rwf=float(amount_rwf or tx_amount),
                    direction=direction,
                    reference=reference,
                    note=note or reference,
                    created_by_id=getattr(current_user, 'id', None),
                )
                if direction == 'IN':
                    account.current_balance = float((account.current_balance or 0.0) + tx_amount)
                else:
                    if float(account.current_balance or 0.0) < float(tx_amount):
                        raise ValueError('Insufficient funds in selected cash account for cash OUT.')
                    account.current_balance = float((account.current_balance or 0.0) - tx_amount)
                db.session.add(tx)
                db.session.add(account)
                db.session.flush()
                review.cash_transaction_id = int(tx.id)
                review.cash_account_id = int(account.id)

                boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                for (boss_id,) in boss_rows:
                    create_notification(
                        user_id=int(boss_id),
                        type_='CASH_TRANSACTION_DISBURSED',
                        message=(
                            f"Umubitsi {getattr(current_user, 'username', 'unknown')} yashyize mu bikorwa cash transaction #{review.id} "
                            f"({direction}) kuri konti {account.name}. Amount: {tx_amount:,.2f} {currency}" + (f" (={float(amount_rwf or 0.0):,.2f} RWF)" if currency == 'USD' else "") + "."
                        ),
                        related_type='payment_review',
                        related_id=int(review.id),
                    )

        else:
            # Supplier / worker requests: execute the real payment now AND post the cash movement.
            tx_amount = float(amount_cash or 0.0)
            if tx_amount <= 0:
                raise ValueError('Amount must be > 0.')

            if method == 'CASH':
                if not chosen_account_id:
                    flash('Select a cash account to disburse this approved request.', 'danger')
                    return redirect(url_for('core.cashier_dashboard'))
                cash_account = CashAccount.query.get(int(chosen_account_id))
                if not cash_account:
                    flash('Selected cash account not found.', 'danger')
                    return redirect(url_for('core.cashier_dashboard'))
                if float(cash_account.current_balance or 0.0) < float(tx_amount):
                    raise ValueError('Insufficient funds in selected cash account for disbursement.')
                if (cash_account.currency or 'RWF').upper() != (currency or 'RWF').upper():
                    raise ValueError('Selected cash account currency does not match request currency.')

                cash_tx = CashTransaction(
                    account_id=cash_account.id,
                    amount=tx_amount,
                    currency=currency,
                    exchange_rate=exchange_rate,
                    amount_input=amount_input,
                    amount_rwf=amount_rwf,
                    direction='OUT',
                    reference=reference,
                    note=note or reference,
                    created_by_id=getattr(current_user, 'id', None),
                )
                cash_account.current_balance = float((cash_account.current_balance or 0.0) - tx_amount)
                db.session.add(cash_tx)
                db.session.add(cash_account)
                db.session.flush()
                review.cash_transaction_id = int(cash_tx.id)
                review.cash_account_id = int(cash_account.id)
            else:
                # BANK/MOMO/etc - do not touch cash accounts.
                review.cash_transaction_id = None
                review.cash_account_id = None

            if ("supplier" in review_type) or ("utanga" in review_type):
                payment_kind = (payload.get('payment_kind') or 'settlement').strip().lower()
                supplier_name = (payload.get('supplier_name') or review.customer or '').strip() or None
                supplier_id = payload.get('supplier_id')
                try:
                    supplier_id = int(supplier_id) if supplier_id not in (None, '') else None
                except Exception:
                    supplier_id = None

                if mineral in {'coltan', 'copper'}:
                    from copper.models import SupplierPayment, CopperStock, CopperSupplier, CopperAdvanceAllocation
                    from sqlalchemy import func as _func

                    def _resolve_copper_supplier_id(name):
                        clean = (name or '').strip()
                        if not clean:
                            return None
                        row = CopperSupplier.query.filter(_func.lower(CopperSupplier.name) == clean.lower()).first()
                        if row:
                            return int(row.id)
                        row = CopperSupplier(name=clean)
                        db.session.add(row)
                        db.session.flush()
                        return int(row.id)

                    if payment_kind == 'settlement':
                        stock_id = payload.get('stock_id')
                        if not stock_id:
                            raise ValueError('Missing stock for supplier settlement request.')
                        stock = CopperStock.query.filter(
                            CopperStock.id == stock_id,
                            CopperStock.is_deleted.is_(False),
                        ).first()
                        if not stock:
                            raise ValueError('Stock not found for supplier settlement request.')
                        if float(amount_rwf) > float(stock.remaining_to_pay() or 0.0):
                            raise ValueError('Requested payment now exceeds remaining supplier debt.')
                        if supplier_id is None:
                            supplier_id = _resolve_copper_supplier_id(stock.supplier)
                        payment = SupplierPayment(
                            stock_id=stock.id,
                            supplier_id=supplier_id,
                            supplier_name=stock.supplier,
                            amount=amount_rwf,
                            input_amount=amount_input,
                            currency=currency,
                            exchange_rate=exchange_rate,
                            amount_rwf=amount_rwf,
                            method=method,
                            reference=reference,
                            note=note,
                            payment_type='SETTLEMENT',
                            is_advance=False,
                            advance_remaining=0.0,
                        )
                        db.session.add(payment)
                        db.session.flush()
                        review.payment_id = int(payment.id)

                        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
                        for (boss_id,) in boss_rows:
                            create_notification(
                                user_id=int(boss_id),
                                type_='SUPPLIER_PAYMENT_DISBURSED',
                                message=(
                                    f"Umubitsi {getattr(current_user, 'username', 'unknown')} yashyize mu bikorwa kwishyura supplier "
                                    f"{supplier_name or stock.supplier if stock else (review.customer or 'N/A')}. Amount: {tx_amount:,.2f} {currency}" + (f" (={float(amount_rwf or 0.0):,.2f} RWF)" if currency == 'USD' else "") + "."
                                ),
                                related_type='payment_review',
                                related_id=int(review.id),
                            )
                    else:
                        if supplier_id is None:
                            supplier_id = _resolve_copper_supplier_id(supplier_name)
                        payment = SupplierPayment(
                            supplier_id=supplier_id,
                            supplier_name=supplier_name,
                            stock_id=None,
                            amount=amount_rwf,
                            input_amount=amount_input,
                            currency=currency,
                            exchange_rate=exchange_rate,
                            amount_rwf=amount_rwf,
                            method=method,
                            reference=reference,
                            note=note,
                            payment_type='ADVANCE',
                            is_advance=True,
                            advance_remaining=float(amount_rwf or 0.0),
                        )
                        db.session.add(payment)
                        db.session.flush()
                        review.payment_id = int(payment.id)

                        try:
                            from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation

                            def _norm_supplier(nm):
                                return ' '.join((nm or '').strip().lower().split())

                            unified = UnifiedSupplierAdvance(
                                supplier_name=(supplier_name or '').strip() or (review.customer or '').strip() or 'Unknown',
                                supplier_name_norm=_norm_supplier(supplier_name),
                                source_mineral_type='copper',
                                source_payment_id=int(payment.id),
                                input_amount=float(amount_input) if amount_input is not None else None,
                                currency=(currency or 'RWF'),
                                exchange_rate=float(exchange_rate or 1.0),
                                amount_rwf=float(amount_rwf or 0.0),
                                paid_at=payment.paid_at,
                                method=method,
                                reference=reference,
                                note=note,
                                advance_remaining=float(amount_rwf or 0.0),
                                created_by_id=getattr(review, 'created_by_id', None),
                            )
                            db.session.add(unified)
                            db.session.flush()
                        except Exception:
                            unified = None

                        remaining_advance = float(amount_rwf or 0.0)
                        supplier_stocks = (
                            CopperStock.query
                            .filter(
                                CopperStock.supplier == supplier_name,
                                CopperStock.net_balance > 0,
                                CopperStock.is_deleted.is_(False),
                            )
                            .order_by(CopperStock.date.asc(), CopperStock.id.asc())
                            .all()
                        )
                        for stock in supplier_stocks:
                            if remaining_advance <= 0:
                                break
                            remaining_for_stock = max(float(stock.remaining_to_pay() or 0.0), 0.0)
                            if remaining_for_stock <= 0:
                                continue
                            alloc = min(remaining_advance, remaining_for_stock)
                            if alloc <= 0:
                                continue
                            db.session.add(CopperAdvanceAllocation(
                                stock_id=stock.id,
                                supplier_payment_id=payment.id,
                                applied_amount=float(alloc),
                            ))
                            if unified is not None:
                                try:
                                    db.session.add(UnifiedSupplierAdvanceAllocation(
                                        advance_id=unified.id,
                                        stock_mineral_type='copper',
                                        stock_id=int(stock.id),
                                        applied_amount=float(alloc),
                                    ))
                                except Exception:
                                    pass
                            remaining_advance -= float(alloc)
                        payment.advance_remaining = max(float(remaining_advance), 0.0)
                        if unified is not None:
                            try:
                                unified.advance_remaining = float(payment.advance_remaining or 0.0)
                                db.session.add(unified)
                            except Exception:
                                pass

                elif mineral == 'cassiterite':
                    from cassiterite.models import CassiteriteSupplierPayment, CassiteriteStock, CassiteriteAdvanceAllocation, CassiteriteSupplier
                    from sqlalchemy import func as _func

                    def _resolve_cass_supplier_id(name):
                        clean = (name or '').strip()
                        if not clean:
                            return None
                        row = CassiteriteSupplier.query.filter(_func.lower(CassiteriteSupplier.name) == clean.lower()).first()
                        if row:
                            return int(row.id)
                        row = CassiteriteSupplier(name=clean)
                        db.session.add(row)
                        db.session.flush()
                        return int(row.id)

                    if payment_kind == 'settlement':
                        stock_id = payload.get('stock_id')
                        if not stock_id:
                            raise ValueError('Missing stock for supplier settlement request.')
                        stock = CassiteriteStock.query.filter(
                            CassiteriteStock.id == stock_id,
                            CassiteriteStock.is_deleted.is_(False),
                        ).first()
                        if not stock:
                            raise ValueError('Stock not found for supplier settlement request.')
                        if float(amount_rwf) > float(stock.remaining_to_pay() or 0.0):
                            raise ValueError('Requested payment now exceeds remaining supplier debt.')
                        if supplier_id is None:
                            supplier_id = _resolve_cass_supplier_id(stock.supplier)
                        payment = CassiteriteSupplierPayment(
                            stock_id=stock.id,
                            supplier_id=supplier_id,
                            supplier_name=stock.supplier,
                            amount=amount_rwf,
                            input_amount=amount_input,
                            currency=currency,
                            exchange_rate=exchange_rate,
                            amount_rwf=amount_rwf,
                            method=method,
                            reference=reference,
                            note=note,
                            payment_type='SETTLEMENT',
                            is_advance=False,
                            advance_remaining=0.0,
                        )
                        db.session.add(payment)
                        db.session.flush()
                        review.payment_id = int(payment.id)
                    else:
                        if supplier_id is None:
                            supplier_id = _resolve_cass_supplier_id(supplier_name)
                        payment = CassiteriteSupplierPayment(
                            supplier_id=supplier_id,
                            supplier_name=supplier_name,
                            stock_id=None,
                            amount=amount_rwf,
                            input_amount=amount_input,
                            currency=currency,
                            exchange_rate=exchange_rate,
                            amount_rwf=amount_rwf,
                            method=method,
                            reference=reference,
                            note=note,
                            payment_type='ADVANCE',
                            is_advance=True,
                            advance_remaining=amount_rwf,
                        )
                        db.session.add(payment)
                        db.session.flush()
                        review.payment_id = int(payment.id)

                        try:
                            from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation

                            def _norm_supplier(nm):
                                return ' '.join((nm or '').strip().lower().split())

                            unified = UnifiedSupplierAdvance(
                                supplier_name=(supplier_name or '').strip() or (review.customer or '').strip() or 'Unknown',
                                supplier_name_norm=_norm_supplier(supplier_name),
                                source_mineral_type='cassiterite',
                                source_payment_id=int(payment.id),
                                input_amount=float(amount_input) if amount_input is not None else None,
                                currency=(currency or 'RWF'),
                                exchange_rate=float(exchange_rate or 1.0),
                                amount_rwf=float(amount_rwf or 0.0),
                                paid_at=payment.paid_at,
                                method=method,
                                reference=reference,
                                note=note,
                                advance_remaining=float(amount_rwf or 0.0),
                                created_by_id=getattr(review, 'created_by_id', None),
                            )
                            db.session.add(unified)
                            db.session.flush()
                        except Exception:
                            unified = None

                        remaining_advance = float(amount_rwf or 0.0)
                        supplier_stocks = (
                            CassiteriteStock.query
                            .filter(
                                CassiteriteStock.supplier == supplier_name,
                                CassiteriteStock.balance_to_pay > 0,
                                CassiteriteStock.is_deleted.is_(False),
                            )
                            .order_by(CassiteriteStock.date.asc(), CassiteriteStock.id.asc())
                            .all()
                        )
                        for stock in supplier_stocks:
                            if remaining_advance <= 0:
                                break
                            remaining_for_stock = max(float(stock.remaining_to_pay() or 0.0), 0.0)
                            if remaining_for_stock <= 0:
                                continue
                            alloc = min(remaining_advance, remaining_for_stock)
                            if alloc <= 0:
                                continue
                            db.session.add(CassiteriteAdvanceAllocation(
                                stock_id=stock.id,
                                supplier_payment_id=payment.id,
                                applied_amount=float(alloc),
                            ))
                            if unified is not None:
                                try:
                                    db.session.add(UnifiedSupplierAdvanceAllocation(
                                        advance_id=unified.id,
                                        stock_mineral_type='cassiterite',
                                        stock_id=int(stock.id),
                                        applied_amount=float(alloc),
                                    ))
                                except Exception:
                                    pass
                            remaining_advance -= float(alloc)
                        payment.advance_remaining = max(float(remaining_advance), 0.0)
                        if unified is not None:
                            try:
                                unified.advance_remaining = float(payment.advance_remaining or 0.0)
                                db.session.add(unified)
                            except Exception:
                                pass
                else:
                    raise ValueError('Unsupported mineral for supplier payment execution.')

            elif ("worker" in review_type) or ("mukozi" in review_type):
                worker_name = payload.get('worker_name') or review.customer
                if mineral in {'coltan', 'copper'}:
                    from copper.models import WorkerPayment
                    payment = WorkerPayment(
                        worker_name=worker_name,
                        amount=amount_rwf,
                        method=method,
                        reference=reference,
                        note=note,
                    )
                elif mineral == 'cassiterite':
                    from cassiterite.models.workers_payment import CassiteriteWorkerPayment
                    payment = CassiteriteWorkerPayment(
                        worker_name=worker_name,
                        amount=amount_rwf,
                        method=method,
                        reference=reference,
                        note=note,
                    )
                else:
                    raise ValueError('Unsupported mineral for worker payment execution.')
                db.session.add(payment)
                db.session.flush()
                review.payment_id = int(payment.id)

        review.disbursement_status = 'DISBURSED'
        review.disbursed_by_id = getattr(current_user, 'id', None)
        review.disbursed_at = datetime.utcnow()
        db.session.add(review)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Disbursement failed: {e}", 'danger')
        return redirect(url_for('core.cashier_dashboard'))

    try:
        rt = (review.type or '').strip().lower()
        mineral = (review.mineral_type or '').strip().lower()
        action = ''
        try:
            payload = json.loads(review.request_payload or '{}') if review.request_payload else {}
            if isinstance(payload, dict):
                action = (payload.get('action') or '').strip().lower()
        except Exception:
            action = ''

        if action == 'collect_receipt':
            try:
                receipt_id = int((payload or {}).get('receipt_id') or 0)
            except Exception:
                receipt_id = 0
            if receipt_id:
                return redirect(url_for('core.customer_receipt_detail', receipt_id=receipt_id))

        if action == 'collect_unearned_receipt':
            try:
                unearned_id = int((payload or {}).get('unearned_id') or 0)
            except Exception:
                unearned_id = 0
            if unearned_id:
                return redirect(url_for('core.customer_unearned_receipt_detail', unearned_id=unearned_id))

        if review.payment_id and (('supplier' in rt) or ('utanga' in rt)):
            if mineral in {'copper', 'coltan'}:
                return redirect(url_for('copper.supplier_receipt', payment_id=int(review.payment_id)))
            if mineral == 'cassiterite':
                return redirect(url_for('cassiterite.supplier_receipt', payment_id=int(review.payment_id)))
        if review.payment_id and (('worker' in rt) or ('mukozi' in rt)):
            if mineral in {'copper', 'coltan'}:
                return redirect(url_for('copper.worker_receipt', payment_id=int(review.payment_id)))
            if mineral == 'cassiterite':
                return redirect(url_for('cassiterite.worker_receipt', payment_id=int(review.payment_id)))
    except Exception:
        pass

    flash('Request disbursed successfully.', 'success')
    return redirect(url_for('core.cashier_dashboard'))


@core_bp.route("/cashier/dashboard", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_dashboard():
    if request.method == "POST":
        return redirect(url_for('core.cashier_approved_requests'))
    return redirect(url_for('core.cashier_approved_requests'))
