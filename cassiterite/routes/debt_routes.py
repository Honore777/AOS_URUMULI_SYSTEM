"""Cassiterite Debt Routes

Handles negotiator-led customer debt tracking and customer payments for cassiterite.

Boss/admin can view the debt ledger, but payment writes stay with the
negotiator because they own customer settlement entry.
"""
from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user
from sqlalchemy import func
from config import db
from cassiterite.models import CassiteriteOutput
from cassiterite.forms import RecordCassiteritePaymentForm
from cassiterite.routes import cassiterite_bp
from core.auth import role_required
from core.models import PaymentReview, User, create_notification


def _normalize_amount_to_rwf(amount, currency, exchange_rate):
    currency_code = (currency or 'RWF').upper()
    input_amount = float(amount or 0)
    rate = float(exchange_rate or 0)

    if currency_code == 'RWF':
        return input_amount, 1.0
    if currency_code == 'USD':
        if rate <= 0:
            raise ValueError('Exchange rate is required and must be greater than 0 for USD payments.')
        return input_amount * rate, rate
    raise ValueError(f'Unsupported currency: {currency_code}')


def _populate_customer_choices(form: RecordCassiteritePaymentForm) -> None:
    """Populate dropdown with customers that still have cassiterite debt.

    Choices look like: "CustomerName - Remaining: 123,456.78 RWF".
    """
    customers_with_debt = (
        db.session.query(
            CassiteriteOutput.customer,
            func.sum(CassiteriteOutput.debt_remaining).label('total_debt'),
        )
        .filter(CassiteriteOutput.debt_remaining > 0)
        .group_by(CassiteriteOutput.customer)
        .all()
    )

    form.customer.choices = [
        (
            row.customer,
            f"{row.customer} - Remaining: {row.total_debt:,.2f} RWF",
        )
        for row in customers_with_debt
        if row.customer
    ]


@cassiterite_bp.route('/track_debts', methods=['GET', 'POST'])
@role_required("negotiator", "boss", "admin")
def track_debts():
    """Track cassiterite customer debts"""
    form = RecordCassiteritePaymentForm()
    _populate_customer_choices(form)

    selected_customer = None

    # Base query: all outputs that still have remaining debt
    debts_query = CassiteriteOutput.query.filter(
        CassiteriteOutput.debt_remaining > 0
    )

    if request.method == 'POST' and form.validate_on_submit():
        selected_customer = form.customer.data
        debts_query = debts_query.filter(CassiteriteOutput.customer == selected_customer)

    filtered_debts = debts_query.order_by(CassiteriteOutput.date).all()
    
    return render_template(
        'cassiterite/debt_tracking.html',
        form=form,
        debts=filtered_debts,
        selected_customer=selected_customer
    )


@cassiterite_bp.route('/update_payment', methods=['POST'])
@role_required("negotiator", "admin")
def update_payment():
    """Update customer payment for cassiterite"""
    if getattr(current_user, 'role', None) not in {'negotiator', 'admin'}:
        flash('Only negotiator can record debt payments. Boss/admin have read-only visibility.', 'warning')
        return redirect(url_for('cassiterite.track_debts'))

    form = RecordCassiteritePaymentForm()
    _populate_customer_choices(form)

    if form.validate_on_submit():
        customer_name = form.customer.data
        payment_amount = float(form.payment_amount.data)
        currency = (getattr(form, 'currency', None).data if hasattr(form, 'currency') else 'RWF') or 'RWF'
        currency = currency.upper()
        exchange_rate_input = getattr(form, 'exchange_rate', None).data if hasattr(form, 'exchange_rate') else 1.0
        try:
            payment_amount_rwf, exchange_rate = _normalize_amount_to_rwf(payment_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for("cassiterite.track_debts"))
        
        outputs_with_debt = (
            CassiteriteOutput.query.filter(CassiteriteOutput.customer == customer_name)
            .filter(CassiteriteOutput.debt_remaining > 0)
            .order_by(CassiteriteOutput.date)
            .all()
        )
        
        remaining_payment = payment_amount_rwf
        
        for output in outputs_with_debt:
            if remaining_payment <= 0:
                break
            
            debt = output.debt_remaining or 0
            
            if remaining_payment >= debt:
                output.amount_paid_rwf = (output.amount_paid_rwf or output.amount_paid or 0) + debt
                output.amount_paid = output.amount_paid_rwf
                output.debt_remaining = 0
                remaining_payment -= debt
            else:
                # Partial payment
                output.amount_paid_rwf = (output.amount_paid_rwf or output.amount_paid or 0) + remaining_payment
                output.amount_paid = output.amount_paid_rwf
                output.debt_remaining -= remaining_payment
                remaining_payment = 0
            
            db.session.add(output)

        # Create a PaymentReview entry so the boss can approve this
        # cassiterite customer payment from the boss dashboard.
        review = PaymentReview(
            mineral_type="cassiterite",
            type="customer",
            customer=customer_name,
            amount=payment_amount_rwf,
            currency="RWF",
            payment_id=None,
            created_by_id=current_user.id,
        )
        db.session.add(review)

        # Notify all active bosses that a cassiterite payment is
        # waiting for review (ids only)
        boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
        message = (
            f"Hasabwe kwemeza: Kwishyura umukiriya kuri Gasegereti - {customer_name}, Amafaranga: {payment_amount_rwf:,.2f} RWF ({payment_amount:,.2f} {currency})."
        )
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=boss_id,
                type_="PAYMENT_REVIEW_CREATED",
                message=message,
                related_type="payment_review",
                related_id=review.id,
            )

        db.session.commit()
        flash(f"Payment of {payment_amount_rwf:,.2f} RWF ({payment_amount:,.2f} {currency}) applied to {customer_name} and sent for boss review.", "success")
    
    else:
        flash("Invalid form submission. Please check the inputs.", "error")
    
    return redirect(url_for("cassiterite.track_debts"))


@cassiterite_bp.route('/customer_ledger/<customer>')
def customer_ledger(customer):
    """Legacy route kept for compatibility; use unified receipts ledger."""
    return redirect(url_for('core.cassiterite_customer_ledger', customer=customer))
