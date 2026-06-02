"""Debt Routes
Handles negotiator-led customer debt tracking and customer payments for copper.

IMPORTANT:
- Negotiators record customer payments and debt changes.
- Boss/admin can view the ledger, but payment writes stay negotiator-owned.
"""
from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user

from config import db
from copper.models import CopperOutput
from copper import copper_bp
from core.auth import role_required
from core.models import PaymentReview, User, create_notification
from sqlalchemy import func


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


@copper_bp.route('/track_debts', methods=['GET', 'POST'])
@role_required("negotiator", "boss", "admin")
def track_debts():
        """Track copper customer debts"""
        from copper.forms import DebtTrackingForm
        
        form = DebtTrackingForm()

        customers_with_debt = (
            CopperOutput.query.filter(CopperOutput.is_deleted.is_(False), CopperOutput.debt_remaining > 0).all()
        )

        selected_customer = None
        filtered_debts = []

        if request.method == 'POST' and form.validate_on_submit():
            selected_customer = form.customer.data
            payment_amount = form.payment_amount.data

            filtered_debts = (
                CopperOutput.query.filter(CopperOutput.is_deleted.is_(False), CopperOutput.customer == selected_customer)
                .filter(CopperOutput.debt_remaining > 0).all()
            )

        else:
            filtered_debts = customers_with_debt

        return render_template(
            'copper/debt_tracking.html',
            form=form,
            debts=filtered_debts,
            selected_customer=selected_customer
        )


@copper_bp.route('/update_payment', methods=['POST'])
@role_required("negotiator", "admin")
def update_payment():
        """Update customer payment for copper"""
        from copper.forms import DebtTrackingForm

        if getattr(current_user, 'role', None) not in {'negotiator', 'admin'}:
            flash('Only negotiator can record debt payments. Boss/admin have read-only visibility.', 'warning')
            return redirect(url_for('copper.track_debts'))

        form = DebtTrackingForm()

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
                return redirect(url_for("copper.track_debts"))

            outputs_with_debt = (
                CopperOutput.query.filter(CopperOutput.customer == customer_name)
                .filter(CopperOutput.debt_remaining > 0)
                .order_by(CopperOutput.date)
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

            # At this point, all affected CopperOutput rows have updated
            # amount_paid / debt_remaining values. We now create a
            # PaymentReview so the boss can see and approve this payment.

            review = PaymentReview(
                mineral_type="coltan",           # identifies the module (display as coltan)
                type="customer",
                customer=customer_name,
                amount=payment_amount_rwf,            # total payment just applied
                currency="RWF",                  # normalized working currency
                payment_id=None,                  # optional, no separate payment table yet
                created_by_id=current_user.id     # the accountant who did this
            )
            db.session.add(review)

            # Optionally notify all active bosses that a new payment
            # is waiting for review on their dashboard (fetch ids only)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            message = (
                f"Hasabwe kwemeza: Kwishyura umukiriya kuri Coltan - {customer_name}, Amafaranga: {payment_amount_rwf:,.2f} RWF ({payment_amount:,.2f} {currency})."
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

        return redirect(url_for("copper.track_debts"))


@copper_bp.route('/customer_ledger/<customer>')
def customer_ledger(customer):
    """Legacy route kept for compatibility; use unified receipts ledger."""
    return redirect(url_for('core.copper_customer_ledger', customer=customer))
