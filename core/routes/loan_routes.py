import logging
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import current_user

from config import db
from core.auth import role_required
from core.models import Loan, LoanLedgerEntry, PaymentReview, PaymentReviewStatus, User, create_notification
from sqlalchemy import func

from . import core_bp

logger = logging.getLogger(__name__)


def _norm_name(nm: str) -> str:
    return ' '.join((nm or '').strip().lower().split())


def _normalize_money(amount_input: float, currency: str, exchange_rate: float) -> tuple[float, float, str, float]:
    currency = (currency or 'RWF').strip().upper()
    exchange_rate = float(exchange_rate or 1.0)
    amount_input = float(amount_input or 0.0)
    if currency == 'USD':
        if exchange_rate <= 0:
            raise ValueError('Exchange rate must be > 0 for USD.')
        return amount_input, float(amount_input * exchange_rate), currency, exchange_rate
    return amount_input, float(amount_input), currency, exchange_rate


@core_bp.route('/accountant/lenders', methods=['GET'])
@role_required('accountant', 'boss', 'admin')
def accountant_lenders():
    rows = (
        db.session.query(
            Loan.lender_name_norm.label('lender_name_norm'),
            func.max(Loan.lender_name).label('lender_name'),
            func.coalesce(func.sum(Loan.outstanding_rwf), 0.0).label('outstanding_rwf'),
            func.coalesce(func.sum(Loan.disbursed_rwf), 0.0).label('disbursed_rwf'),
            func.coalesce(func.sum(Loan.repaid_rwf), 0.0).label('repaid_rwf'),
        )
        .group_by(Loan.lender_name_norm)
        .order_by(func.coalesce(func.sum(Loan.outstanding_rwf), 0.0).desc())
        .all()
    )
    lenders = [
        {
            'lender_name_norm': r.lender_name_norm,
            'lender_name': r.lender_name,
            'outstanding_rwf': float(r.outstanding_rwf or 0.0),
            'disbursed_rwf': float(r.disbursed_rwf or 0.0),
            'repaid_rwf': float(r.repaid_rwf or 0.0),
        }
        for r in rows
        if r and r.lender_name_norm
    ]
    return render_template('accountant/lenders.html', lenders=lenders)


@core_bp.route('/accountant/lenders/pay', methods=['POST'])
@role_required('accountant', 'boss', 'admin')
def accountant_request_lender_payment():
    lender_name = (request.form.get('lender_name') or '').strip()
    if not lender_name:
        flash('Lender name is required.', 'danger')
        return redirect(url_for('core.accountant_lenders'))

    try:
        amount_input = float(request.form.get('amount') or 0.0)
    except Exception:
        amount_input = 0.0
    if amount_input <= 0:
        flash('Amount must be > 0.', 'danger')
        return redirect(url_for('core.accountant_lenders'))

    currency = (request.form.get('currency') or 'RWF').strip().upper()
    try:
        exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    except Exception:
        exchange_rate = 1.0
    try:
        amount_input, amount_rwf, currency, exchange_rate = _normalize_money(amount_input, currency, exchange_rate)
    except Exception as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('core.accountant_lenders'))

    method = (request.form.get('method') or 'CASH').strip().upper()
    note = (request.form.get('note') or '').strip() or None

    lender_norm = _norm_name(lender_name)
    reference = f"lender_payment_request:{lender_norm}"
    dup = (
        PaymentReview.query
        .filter(
            PaymentReview.status.in_([PaymentReviewStatus.PENDING_REVIEW.value, PaymentReviewStatus.APPROVED.value]),
            PaymentReview.disbursement_status == 'NOT_DISBURSED',
            PaymentReview.type == 'loan_repayment',
            PaymentReview.request_payload.contains(reference),
        )
        .first()
    )
    if dup:
        flash('There is already a pending/approved lender payment request for this lender.', 'warning')
        return redirect(url_for('core.accountant_lenders'))

    outstanding_total = (
        db.session.query(func.coalesce(func.sum(Loan.outstanding_rwf), 0.0))
        .filter(Loan.lender_name_norm == lender_norm)
        .scalar()
        or 0.0
    )
    if amount_rwf > float(outstanding_total or 0.0):
        flash('Amount exceeds outstanding balance for this lender.', 'danger')
        return redirect(url_for('core.accountant_lenders'))

    payload = {
        'action': 'loan_repayment',
        'lender_name': lender_name,
        'lender_name_norm': lender_norm,
        'amount': float(amount_rwf),
        'currency': currency,
        'exchange_rate': float(exchange_rate or 1.0),
        'amount_input': float(amount_input),
        'amount_rwf': float(amount_rwf),
        'method': method,
        'note': note or f'Lender payment - {lender_name}',
        'reference': reference,
    }

    review = PaymentReview(
        mineral_type=None,
        type='loan_repayment',
        customer=lender_name,
        amount=float(amount_rwf),
        currency=currency,
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
        request_payload=__import__('json').dumps(payload),
    )
    db.session.add(review)
    db.session.flush()

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        create_notification(
            user_id=int(boss_id),
            type_='LENDER_PAYMENT_REQUESTED',
            message=f"Hariho ubusabe bwo kwishyura uwatanze inguzanyo: {lender_name} ({amount_input:,.2f} {currency} = {amount_rwf:,.2f} RWF).",
            related_type='payment_review',
            related_id=int(review.id),
        )

    db.session.commit()
    flash('Lender payment request submitted for boss approval.', 'success')
    return redirect(url_for('core.accountant_lenders'))


@core_bp.route('/api/lenders/autocomplete')
@role_required('accountant', 'boss', 'admin', 'cashier', 'negotiator')
def lenders_autocomplete():
    q = (request.args.get('q') or '').strip()
    if not q:
        return {'results': []}
    q_norm = _norm_name(q)
    rows = (
        db.session.query(Loan.lender_name)
        .filter(Loan.lender_name_norm.contains(q_norm))
        .distinct()
        .order_by(Loan.lender_name.asc())
        .limit(15)
        .all()
    )
    return {'results': [nm for (nm,) in rows if nm]}


@core_bp.route('/accountant/lenders/<string:lender_norm>', methods=['GET'])
@role_required('accountant', 'boss', 'admin')
def lender_loans(lender_norm: str):
    norm = _norm_name(lender_norm)
    loans = Loan.query.filter(Loan.lender_name_norm == norm).order_by(Loan.created_at.asc(), Loan.id.asc()).all()
    if not loans:
        abort(404)
    lender_name = loans[0].lender_name
    loan_ids = [int(l.id) for l in loans]
    raw_entries = LoanLedgerEntry.query.filter(LoanLedgerEntry.loan_id.in_(loan_ids)).order_by(LoanLedgerEntry.created_at.asc(), LoanLedgerEntry.id.asc()).all()

    entries = []
    running_rwf = 0.0
    total_disbursed = 0.0
    total_repaid = 0.0
    for entry in raw_entries:
        amount_rwf = float(entry.amount_rwf or 0.0)
        amount_input = float(entry.amount_input or 0.0)
        currency = (entry.currency or 'RWF').upper()
        if entry.entry_type == 'DISBURSEMENT':
            running_rwf += amount_rwf
            total_disbursed += amount_rwf
            display_type = 'Loan Disbursement'
        else:
            running_rwf -= amount_rwf
            total_repaid += amount_rwf
            display_type = 'Loan Repayment'
        entries.append({
            'created_at': entry.created_at,
            'entry_type': display_type,
            'amount_input': amount_input,
            'currency': currency,
            'exchange_rate': float(entry.exchange_rate or 1.0),
            'amount_rwf': amount_rwf,
            'balance_rwf': float(running_rwf),
            'note': entry.note,
            'cash_transaction_id': entry.cash_transaction_id,
            'loan_id': entry.loan_id,
        })

    return render_template(
        'accountant/lender_ledger.html',
        lender_name=lender_name,
        entries=entries,
        total_disbursed=total_disbursed,
        total_repaid=total_repaid,
        outstanding_balance=max(running_rwf, 0.0),
    )


@core_bp.route('/receipts/loan/<int:loan_id>', methods=['GET'])
@role_required('accountant', 'boss', 'cashier', 'negotiator', 'admin')
def loan_receipt_detail(loan_id: int):
    loan = Loan.query.get_or_404(loan_id)
    disbursement_entry = (
        LoanLedgerEntry.query
        .filter(LoanLedgerEntry.loan_id == int(loan.id), LoanLedgerEntry.entry_type == 'DISBURSEMENT')
        .order_by(LoanLedgerEntry.created_at.desc(), LoanLedgerEntry.id.desc())
        .first()
    )
    total_disbursed = float(loan.disbursed_rwf or 0.0)
    total_repaid = float(loan.repaid_rwf or 0.0)
    remaining_balance = float(loan.outstanding_rwf or 0.0)
    return render_template(
        'receipts/professional_payment_receipt.html',
        document_title='LOAN RECEIPT FORM',
        document_subtitle='Loan disbursement receipt with approval signatures',
        party_role='Lender',
        party_name=loan.lender_name,
        receipt_reference=f'LOAN-{loan.id:04d}',
        document_date=disbursement_entry.created_at if disbursement_entry else loan.created_at,
        original_amount=loan.principal_input,
        original_currency=loan.currency,
        exchange_rate=loan.exchange_rate,
        paid_amount=disbursement_entry.amount_input if disbursement_entry else loan.principal_input,
        paid_currency=(disbursement_entry.currency if disbursement_entry else loan.currency),
        paid_amount_rwf=disbursement_entry.amount_rwf if disbursement_entry else loan.principal_rwf,
        remaining_amount=remaining_balance,
        remaining_currency='RWF',
        note=loan.note,
        status_label='DISBURSED' if (loan.status or '').upper() == 'DISBURSED' else (loan.status or '-'),
        signers=['Lender', 'Cashier', 'Boss'],
        summary_values={
            'principal_rwf': float(loan.principal_rwf or 0.0),
            'disbursed_rwf': total_disbursed,
            'repaid_rwf': total_repaid,
        },
    )


@core_bp.route('/negotiator/loans', methods=['GET', 'POST'])
@role_required('negotiator', 'admin')
def negotiator_loans():
    if request.method == 'POST':
        lender_name = (request.form.get('lender_name') or '').strip()
        if not lender_name:
            flash('Lender name is required.', 'danger')
            return redirect(url_for('core.negotiator_loans'))

        try:
            principal_input = float(request.form.get('amount') or 0.0)
        except Exception:
            principal_input = 0.0
        if principal_input <= 0:
            flash('Amount must be greater than 0.', 'danger')
            return redirect(url_for('core.negotiator_loans'))

        currency = (request.form.get('currency') or 'RWF').strip().upper()
        try:
            exchange_rate = float(request.form.get('exchange_rate') or 1.0)
        except Exception:
            exchange_rate = 1.0
        if currency == 'USD' and exchange_rate <= 0:
            flash('Exchange rate must be > 0 for USD.', 'danger')
            return redirect(url_for('core.negotiator_loans'))

        principal_rwf = float(principal_input)
        if currency == 'USD':
            principal_rwf = float(principal_input) * float(exchange_rate)

        note = (request.form.get('note') or '').strip() or None

        try:
            loan = Loan(
                lender_name=lender_name,
                lender_name_norm=_norm_name(lender_name),
                principal_input=float(principal_input),
                currency=currency,
                exchange_rate=float(exchange_rate or 1.0),
                principal_rwf=float(principal_rwf),
                outstanding_rwf=float(principal_rwf),
                status='PENDING_APPROVAL',
                created_by_id=getattr(current_user, 'id', None),
                created_at=datetime.utcnow(),
                note=note,
            )
            db.session.add(loan)
            db.session.flush()

            payload = {
                'action': 'loan_disbursement',
                'loan_id': int(loan.id),
                'lender_name': lender_name,
                'currency': currency,
                'exchange_rate': float(exchange_rate or 1.0),
                'amount_input': float(principal_input),
                'amount_rwf': float(principal_rwf),
                'amount': float(principal_rwf),
                'method': 'CASH',
                'note': note or f'Loan request for {lender_name}',
                'reference': f'loan:{int(loan.id)}',
            }

            review = PaymentReview(
                mineral_type=None,
                type='loan_disbursement',
                customer=lender_name,
                amount=float(principal_rwf),
                currency=currency,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=__import__('json').dumps(payload),
            )
            db.session.add(review)
            db.session.flush()

            boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=int(boss_id),
                    type_='LOAN_REQUESTED',
                    message=(
                        f"Hariho inguzanyo nshya isaba kwemezwa: {lender_name} "
                        f"({principal_rwf:,.2f} RWF)."
                    ),
                    related_type='payment_review',
                    related_id=int(review.id),
                )

            db.session.commit()
            flash('Loan request submitted for boss approval.', 'success')
            return redirect(url_for('core.negotiator_loans'))
        except Exception as e:
            db.session.rollback()
            flash(f'Failed to create loan request: {e}', 'danger')
            return redirect(url_for('core.negotiator_loans'))

    loans = Loan.query.order_by(Loan.created_at.desc()).limit(200).all()
    return render_template('negotiator/loans.html', loans=loans)


@core_bp.route('/negotiator/loans/<int:loan_id>', methods=['GET'])
@role_required('negotiator', 'admin', 'boss', 'accountant', 'cashier')
def loan_detail(loan_id: int):
    loan = Loan.query.get_or_404(loan_id)
    entries = LoanLedgerEntry.query.filter_by(loan_id=loan.id).order_by(LoanLedgerEntry.created_at.asc(), LoanLedgerEntry.id.asc()).all()
    return render_template('negotiator/loan_detail.html', loan=loan, entries=entries)


@core_bp.route('/boss/loans', methods=['GET'])
@role_required('boss', 'admin', 'accountant')
def boss_loans():
    loans = Loan.query.order_by(Loan.created_at.desc()).limit(300).all()
    return render_template('boss/loans.html', loans=loans)


@core_bp.route('/boss/loans/<int:loan_id>', methods=['GET'])
@role_required('boss', 'admin', 'accountant')
def boss_loan_detail(loan_id: int):
    loan = Loan.query.get_or_404(loan_id)
    entries = LoanLedgerEntry.query.filter_by(loan_id=loan.id).order_by(LoanLedgerEntry.created_at.asc(), LoanLedgerEntry.id.asc()).all()
    return render_template('boss/loan_detail.html', loan=loan, entries=entries)
