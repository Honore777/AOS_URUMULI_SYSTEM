import logging
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user

from config import db
from core.auth import role_required
from core.models import CashAccount, CashTransaction, CustomerReceipt

from . import core_bp

logger = logging.getLogger(__name__)


@core_bp.route("/cashier/dashboard", methods=["GET", "POST"])
@role_required("cashier", "boss", "admin")
def cashier_dashboard():
    """Simple cashier dashboard to record cash in/out and view recent transactions."""
    # POST creates a new transaction (only cashier allowed by decorator)
    if request.method == "POST":
        # Two modes: manual cash transaction OR collect a pending customer receipt
        if getattr(current_user, 'role', None) != 'cashier':
            flash('Only cashier users can record cash transactions.', 'warning')
            return redirect(url_for('core.cashier_dashboard'))

        collect_receipt_id = int(request.form.get('collect_receipt_id') or 0)
        if collect_receipt_id:
            receipt = CustomerReceipt.query.get(collect_receipt_id)
            if not receipt:
                flash('Receipt not found.', 'danger')
                return redirect(url_for('core.cashier_dashboard'))
            if receipt.payment_channel.upper() != 'CASH':
                flash('This receipt is not a cash receipt.', 'warning')
                return redirect(url_for('core.cashier_dashboard'))
            if receipt.is_collected:
                flash('Receipt already collected.', 'info')
                return redirect(url_for('core.cashier_dashboard'))

            account_id = int(request.form.get('account_id') or 0)
            account = CashAccount.query.get(account_id)
            if not account:
                flash('Selected cash account not found.', 'danger')
                return redirect(url_for('core.cashier_dashboard'))

            # Create cash transaction (IN)
            tx = CashTransaction(
                account_id=account.id,
                amount=receipt.amount_rwf or receipt.amount_input or 0,
                direction='IN',
                note=f"Collect receipt #{receipt.id}",
                created_by_id=getattr(current_user, 'id', None),
            )
            account.current_balance = float((account.current_balance or 0) + (tx.amount or 0))
            receipt.is_collected = True
            receipt.collected_by_id = getattr(current_user, 'id', None)
            receipt.collected_at = datetime.utcnow()
            receipt.cash_account_id = account.id

            db.session.add(tx)
            db.session.add(account)
            db.session.add(receipt)
            db.session.commit()
            flash(f"Receipt #{receipt.id} collected and recorded to account {account.name}.", 'success')
            return redirect(url_for('core.cashier_dashboard'))

        # Manual cash transaction
        account_id = int(request.form.get('account_id') or 0)
        amount = float(request.form.get('amount') or 0)
        direction = (request.form.get('direction') or 'IN').upper()
        note = request.form.get('note') or None

        account = CashAccount.query.get(account_id)
        if not account:
            flash('Selected cash account not found.', 'danger')
            return redirect(url_for('core.cashier_dashboard'))
        if amount <= 0:
            flash('Amount must be greater than zero.', 'danger')
            return redirect(url_for('core.cashier_dashboard'))

        tx = CashTransaction(
            account_id=account.id,
            amount=amount,
            direction=direction,
            note=note,
            created_by_id=getattr(current_user, 'id', None),
        )

        # Apply balance change immediately
        if direction == 'IN':
            account.current_balance = float((account.current_balance or 0) + amount)
        else:
            account.current_balance = float((account.current_balance or 0) - amount)

        db.session.add(tx)
        db.session.add(account)
        db.session.commit()
        flash('Cash transaction recorded successfully.', 'success')
        return redirect(url_for('core.cashier_dashboard'))

    # GET shows the dashboard
    accounts = CashAccount.query.order_by(CashAccount.name).all()
    recent = CashTransaction.query.order_by(CashTransaction.created_at.desc()).limit(200).all()
    pending_receipts = (
        CustomerReceipt.query
        .filter(CustomerReceipt.payment_channel == 'CASH', CustomerReceipt.is_collected == False)
        .order_by(CustomerReceipt.created_at.asc())
        .limit(200)
        .all()
    )
    return render_template('cashier/dashboard.html', accounts=accounts, recent=recent, pending_receipts=pending_receipts, cash_accounts=accounts)
