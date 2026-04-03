"""
Payment Routes
Handles supplier and worker payment recording for copper.
"""
from flask import render_template, redirect, url_for, flash, abort

from config import db
from copper.models import CopperStock, SupplierPayment, WorkerPayment
from copper.forms import SupplierPaymentForm, WorkerPaymentForm
from copper import copper_bp
from core.auth import role_required
from flask import request
from sqlalchemy import func


@copper_bp.route('/supplier/payment/<int:payment_id>/receipt')
@role_required('accountant')
def supplier_receipt(payment_id):
    """
    Shows a printable receipt for a copper supplier payment.
    """
    payment = SupplierPayment.query.get(payment_id)
    if not payment:
        abort(404)

    stock = CopperStock.query.get(payment.stock_id)
    supplier_name = stock.supplier if stock else "Unknown"

    # total paid including this payment (safe when viewing after save)
    total_paid = db.session.query(
        func.coalesce(func.sum(SupplierPayment.amount), 0)
    ).filter(SupplierPayment.stock_id == stock.id).scalar() or 0.0

    # remaining after this payment has been applied
    remaining_after = (stock.net_balance or 0.0) - total_paid

    # remaining before this payment (useful to show previous balance)
    remaining_before = remaining_after + (payment.amount or 0.0)

    return render_template(
        'receipts/copper_supplier_receipt.html',
        payment=payment,
        supplier_name=supplier_name,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
    )


@copper_bp.route('/worker/payment/<int:payment_id>/receipt')
@role_required('accountant')
def worker_receipt(payment_id):
    """
    Shows a printable receipt for a copper worker payment.
    """
    payment = WorkerPayment.query.get(payment_id)
    if not payment:
        abort(404)
    return render_template('receipts/copper_worker_receipt.html', payment=payment)


@copper_bp.route('/pay_supplier', methods=['GET', 'POST'])
@role_required('accountant')
def pay_supplier():
    """Record supplier payments for copper stocks."""
    from flask import current_app
    from flask_login import current_user
    from core.models import PaymentReview, create_notification, User
    from utils import send_brevo_email_async
    from copper.forms import SupplierPaymentForm

    form = SupplierPaymentForm()

    # populate stock choices - select only required columns, compute remaining via grouped aggregate
    stock_rows = db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.supplier, CopperStock.net_balance).filter(CopperStock.net_balance > 0).order_by(CopperStock.date.desc()).all()
    stock_ids = [r.id for r in stock_rows]
    if stock_ids:
        paid_rows = (
            db.session.query(
                SupplierPayment.stock_id,
                func.coalesce(func.sum(SupplierPayment.amount), 0).label('paid')
            )
            .filter(SupplierPayment.stock_id.in_(stock_ids))
            .group_by(SupplierPayment.stock_id)
            .all()
        )
        paid_map = {r.stock_id: float(r.paid) for r in paid_rows}
    else:
        paid_map = {}

    form.stock_id.choices = []
    for r in stock_rows:
        remaining = (r.net_balance or 0) - paid_map.get(r.id, 0.0)
        if remaining > 0:
            form.stock_id.choices.append((r.id, f"{r.voucher_no} - {r.supplier} - Remaining: {remaining:,.2f} RWF"))

    if form.validate_on_submit():
        stock = CopperStock.query.get_or_404(form.stock_id.data)
        amount = form.amount.data

        if amount > stock.remaining_to_pay():
            flash(f"Payment exceeds remaining balance ({stock.remaining_to_pay()} RWF).", "danger")
            return render_template('copper/pay_supplier.html', form=form)

        try:
            payment = SupplierPayment(
                stock_id=stock.id,
                amount=amount,
                method=form.method.data,
                reference=form.reference.data,
                note=form.note.data,
            )
            db.session.add(payment)
            db.session.commit()

            # create or update a pending PaymentReview for this payment
            from core.models import PaymentReviewStatus
            existing = PaymentReview.query.filter_by(
                payment_id=payment.id,
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                existing.mineral_type = 'coltan'
                existing.type = 'utanga ibicuruzwa'
                existing.customer = stock.supplier
                existing.amount = amount
                existing.currency = 'RWF'
                existing.created_by_id = getattr(current_user, 'id', None)
            else:
                review = PaymentReview(
                    mineral_type='coltan',
                    type='utanga ibicuruzwa',
                    customer=stock.supplier,
                    amount=amount,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                )
                db.session.add(review)
            db.session.commit()

            # in-app notification
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='kwishyura utanga ibicuruzwa',
                    message=f"Hasabwe kwemeza: Kwishyura utanga ibicuruzwa kuri Coltan - {stock.supplier}, Amafaranga: {amount} RWF.",
                    related_type='supplier_payment',
                    related_id=payment.id,
                )
            # Persist in-app notification before attempting email
            db.session.commit()

            # email notification (non-blocking)
            boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
            payment_details = (
                f"utanga amabuye: {stock.supplier}, Amafaranga: {amount} RWF, Uburyo: {form.method.data}, "
                f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
            )
            subject = "Saba Kwemezwa: Kwishyura utanga Ibicuruzwa (Coltan)"
            html_content = (
                "<p>Nyakubahwa Muyobozi,</p>"
                f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) "
                f"yasabye kwemeza ubwishyu bukurikira kuri Coltan:</p>"
                f"<p>{payment_details}</p>"
                "<p>Nyamuneka musuzume kandi mwemeze. Mujye muri Sisiteme kwemeza iki gikorwa</p>"
                "<p>Murakoze,<br>Urumuli Smart System</p>"
            )
            try:
                send_brevo_email_async(subject, html_content, boss_email)
            except Exception:
                import logging
                logging.exception("Failed to send supplier payment email")
                flash("Email notification failed; in-app notification saved.", "warning")

            flash(f"Payment of {amount} RWF recorded for {stock.supplier}.", "success")
            return redirect(url_for('copper.pay_supplier'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving payment: {e}", "danger")
            return render_template('copper/pay_supplier.html', form=form)

    # GET or not-submitted
    # build simple supplier summaries for display using grouped queries to avoid per-row queries
    supplier_summaries = []
    stocks = stock_rows
    # Fetch payments for these stocks and group them
    payments_map = {}
    if stock_ids:
        payments_q = (
            db.session.query(SupplierPayment)
            .filter(SupplierPayment.stock_id.in_(stock_ids))
            .order_by(SupplierPayment.paid_at)
            .all()
        )
        for p in payments_q:
            payments_map.setdefault(p.stock_id, []).append(p)

    for r in stocks:
        total_paid = paid_map.get(r.id, 0.0)
        remaining = (r.net_balance or 0) - total_paid
        if remaining <= 0:
            continue
        payments = []
        for p in payments_map.get(r.id, []):
            paid_at = getattr(p, 'paid_at', None)
            date_str = paid_at.strftime('%Y-%m-%d') if paid_at else ''
            payments.append({'id': p.id, 'amount': float(p.amount), 'date': date_str})

        supplier_summaries.append({
            'stock_id': r.id,
            'supplier': r.supplier,
            'voucher_no': r.voucher_no,
            'net_balance': float(r.net_balance or 0),
            'total_paid': float(total_paid),
            'remaining': float(remaining),
            'payments': payments,
        })

    return render_template('copper/pay_supplier.html', form=form, supplier_summaries=supplier_summaries)


@copper_bp.route('/pay_worker', methods=['GET', 'POST'])
@role_required('accountant')
def pay_worker():
    """Record internal worker payments/expenses for copper."""
    from flask import current_app
    from flask_login import current_user
    from core.models import PaymentReview, create_notification, User

    form = WorkerPaymentForm()

    if form.validate_on_submit():
        try:
            payment = WorkerPayment(
                worker_name=form.worker_name.data,
                amount=form.amount.data,
                method=form.method.data,
                reference=form.reference.data,
                note=form.note.data,
            )
            db.session.add(payment)
            db.session.commit()
            db.session.flush()  # ensure payment.id is populated

            # upsert pending review for this worker payment
            from core.models import PaymentReviewStatus
            existing = PaymentReview.query.filter_by(
                payment_id=payment.id,
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                existing.mineral_type = None
                existing.type = 'umukozi'
                existing.customer = form.worker_name.data
                existing.amount = form.amount.data
                existing.currency = 'RWF'
                existing.created_by_id = getattr(current_user, 'id', None)
            else:
                review = PaymentReview(
                    type='umukozi',
                    customer=form.worker_name.data,
                    amount=form.amount.data,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                )
                db.session.add(review)
            db.session.commit()

            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='PAYMENT_EXECUTED',
                    message=f"Hasabwe kwemeza: Kwishyura umukozi  - {form.worker_name.data}, Amafaranga: {form.amount.data} RWF.",
                    related_type='kwishyura umukozi',
                    related_id=payment.id,
                )
            # Persist in-app notification before attempting email
            db.session.commit()

            from utils import send_brevo_email_async

            boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
            payment_details = (
                f"Umukozi: {form.worker_name.data}, Amafaranga: {form.amount.data} RWF, Uburyo: {form.method.data}, "
                f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
            )
            subject = "Saba Kwemezwa: Kwishyura Umukozi "
            html_content = (
                "<p>Nyakubahwa Muyobozi,</p>"
                f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) "
                f"yasabye kwemeza ubwishyu bukurikira :</p>"
                f"<p>{payment_details}</p>"
                "<p>Musuzume kandi mwemeze.</p>"
                "<p>Murakoze,<br>Urumuli Smart System</p>"
            )
            try:
                send_brevo_email_async(subject, html_content, boss_email)
            except Exception:
                import logging
                logging.exception("Failed to send worker payment email")
                flash("Email notification failed; in-app notification saved.", "warning")

            flash(f"Payment of {form.amount.data} RWF recorded for {form.worker_name.data}.", "success")
            return redirect(url_for('copper.pay_worker'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving payment: {e}", "danger")
            return render_template('copper/pay_worker.html', form=form)

    recent_payments = WorkerPayment.query.order_by(WorkerPayment.paid_at.desc()).limit(15).all()
    return render_template('copper/pay_worker.html', form=form, recent_payments=recent_payments)


@copper_bp.route('/supplier/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_supplier_payment(payment_id):
    from copper.forms import SupplierPaymentForm
    from copper.models import SupplierPayment
    from copper.models import CopperStock
    from core.models import PaymentReview

    payment = SupplierPayment.query.get_or_404(payment_id)
    form = SupplierPaymentForm()

    # Use column-only query for choices
    stock_rows = db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.supplier).order_by(CopperStock.date.desc()).all()
    form.stock_id.choices = [(r.id, f"{r.voucher_no} - {r.supplier}") for r in stock_rows]

    if form.validate_on_submit():
        # When editing an existing payment we must have a change reason.
        if not (form.change_reason.data and form.change_reason.data.strip()):
            flash('Change reason is required when editing a payment.', 'danger')
            return render_template('copper/edit_supplier_payment.html', form=form, payment=payment)

        try:
            stock = CopperStock.query.get(form.stock_id.data)
            payment.stock_id = stock.id
            payment.amount = form.amount.data
            payment.method = form.method.data
            payment.reference = form.reference.data
            payment.note = form.note.data
            db.session.add(payment)
            db.session.commit()
            db.session.flush()  # ensure payment.id is populated for the review record  
            # Create a new PaymentReview for the boss to review this change.
            # We keep existing review rows untouched (they represent what was
            # previously recorded) and create a fresh PENDING_REVIEW entry
            # that contains the new values and the accountant's reason.
            from flask_login import current_user
            # upsert pending review for this edited supplier payment (include change reason)
            from core.models import PaymentReviewStatus
            existing = PaymentReview.query.filter_by(
                payment_id=payment.id,
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                existing.mineral_type = 'coltan'
                existing.type = 'Utanga amabuye'
                existing.customer = stock.supplier
                existing.amount = payment.amount
                existing.currency = 'RWF'
                existing.created_by_id = getattr(current_user, 'id', None)
                existing.boss_comment = (f"Edit requested: {form.change_reason.data.strip()}")
            else:
                review = PaymentReview(
                    mineral_type='coltan',
                    type='Utanga amabuye',
                    customer=stock.supplier,
                    amount=payment.amount,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                    boss_comment=(f"Edit requested: {form.change_reason.data.strip()}"),
                )
                db.session.add(review)
            # in-app notification and email to boss
            from core.models import create_notification, User
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='Guhindura ibyakozwe mbere',
                    message=f"Hasabwe gusuzuma: Impinduka kuri kwishyura utanga ibicuruzwa - {stock.supplier}, Amafaranga: {payment.amount} RWF. Impamvu: {form.change_reason.data.strip()}",
                    related_type='supplier_payment',
                    related_id=payment.id,
                )
            db.session.commit()

            # send email (best-effort)
            try:
                from utils import send_brevo_email_async
                boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
                subject = "Saba Kwemezwa: Impinduka kuri Kwishyura utanga ibicuruzwa (Coltan)"
                html_content = (
                    "<p>Nyakubahwa Muyobozi,</p>"
                    f"<p>Umucungamutungo {getattr(current_user,'username','Unknown')} "
                    f"yasabye ko musuzuma impinduka zikurikira kuri kwishyura utanga amabuye kuri Coltan:</p>"
                    f"<p>Umutanga: {stock.supplier}<br>Amafaranga (byahinduwe): {payment.amount} RWF<br>Impamvu: {form.change_reason.data.strip()}</p>"
                    "<p>Murakoze,<br>Mujye Muri system kwemeza iki gikorwa.<br>Urumuli Smart System</p>"
                )
                try:
                    send_brevo_email_async(subject, html_content, boss_email)
                except Exception:
                    import logging
                    logging.exception("Failed to send supplier edit email")
                    flash("Email notification failed; in-app notification saved.", "warning")
            except Exception:
                pass

            flash('Supplier payment updated; boss has been notified to review the change.', 'success')
            return redirect(url_for('copper.pay_supplier'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating payment: {e}', 'danger')

    if not form.is_submitted():
        form.stock_id.data = payment.stock_id
        form.amount.data = payment.amount
        form.method.data = payment.method
        form.reference.data = payment.reference
        form.note.data = payment.note

    return render_template('copper/edit_supplier_payment.html', form=form, payment=payment)


@copper_bp.route('/supplier/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_supplier_payment(payment_id):
    from copper.models import SupplierPayment, CopperStock
    from core.models import PaymentReview

    payment = SupplierPayment.query.get_or_404(payment_id)
    stock = CopperStock.query.get(payment.stock_id)
    # Require a reason for deletion (submitted via hidden input)
    reason = request.form.get('change_reason', '')
    if not reason or not reason.strip():
        flash('Delete reason is required.', 'danger')
        return redirect(url_for('copper.pay_supplier'))

    try:
        # Create a PaymentReview so the boss can approve the deletion.
        from flask_login import current_user
        from core.models import create_notification, User
        # upsert pending review for delete-request
        from core.models import PaymentReviewStatus
        existing = PaymentReview.query.filter_by(
            payment_id=payment.id,
            status=PaymentReviewStatus.PENDING_REVIEW.value,
        ).first()
        if existing:
            existing.mineral_type = 'coltan'
            existing.type = 'Utanga amabuye'
            existing.customer = stock.supplier
            existing.amount = payment.amount
            existing.currency = 'RWF'
            existing.created_by_id = getattr(current_user, 'id', None)
            existing.boss_comment = (f"Delete requested: {reason.strip()}")
        else:
            review = PaymentReview(
                mineral_type='coltan',
                type='Utanga amabuye',
                customer=stock.supplier,
                amount=payment.amount,
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment=(f"Delete requested: {reason.strip()}"),
            )
            db.session.add(review)
        boss_user = User.query.filter_by(role='boss').first()
        if boss_user:
            create_notification(
                user_id=boss_user.id,
                type_='PAYMENT_DELETE_REQUEST',
                message=f"Hasabwe gusuzuma: Gusiba kwishyura utanga amabuye (Coltan) - {stock.supplier}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {reason.strip()}",
                related_type='supplier_payment',
                related_id=payment.id,
            )
        db.session.commit()
        flash('Delete request submitted for boss review; payment was not deleted until approval.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error submitting delete request: {e}', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting payment: {e}', 'danger')

    return redirect(url_for('copper.pay_supplier'))


@copper_bp.route('/worker/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_worker_payment(payment_id):
    from copper.forms import WorkerPaymentForm
    from copper.models import WorkerPayment
    from core.models import PaymentReview

    payment = WorkerPayment.query.get_or_404(payment_id)
    form = WorkerPaymentForm()

    if form.validate_on_submit():
        # Must provide a reason for edits
        if not (form.change_reason.data and form.change_reason.data.strip()):
            flash('Change reason is required when editing a payment.', 'danger')
            return render_template('copper/edit_worker_payment.html', form=form, payment=payment)

        try:
            payment.worker_name = form.worker_name.data
            payment.amount = form.amount.data
            payment.method = form.method.data
            payment.reference = form.reference.data
            payment.note = form.note.data
            db.session.add(payment)
            db.session.commit()

            # Create a PENDING review for the boss to inspect this edit
            from flask_login import current_user
            from core.models import create_notification, User
            # upsert pending review for edited worker payment
            from core.models import PaymentReviewStatus
            existing = PaymentReview.query.filter_by(
                payment_id=payment.id,
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                existing.mineral_type = None
                existing.type = 'Umukozi'
                existing.customer = payment.worker_name
                existing.amount = payment.amount
                existing.currency = 'RWF'
                existing.created_by_id = getattr(current_user, 'id', None)
                existing.boss_comment = (f"Edit requested: {form.change_reason.data.strip()}")
            else:
                review = PaymentReview(
                    mineral_type=None,
                    type='Umukozi',
                    customer=payment.worker_name,
                    amount=payment.amount,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                    boss_comment=(f"Edit requested: {form.change_reason.data.strip()}"),
                )
                db.session.add(review)
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='PAYMENT_EDIT_REQUEST',
                    message=f"Hasabwe gusuzuma: Impinduka kuri kwishyura umukozi - {payment.worker_name}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {form.change_reason.data.strip()}",
                    related_type='worker_payment',
                    related_id=payment.id,
                )
            db.session.commit()
            flash('Worker payment updated; boss has been notified to review the change.', 'success')
            return redirect(url_for('copper.pay_worker'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating payment: {e}', 'danger')

    if not form.is_submitted():
        form.worker_name.data = payment.worker_name
        form.amount.data = payment.amount
        form.method.data = payment.method
        form.reference.data = payment.reference
        form.note.data = payment.note

    return render_template('copper/edit_worker_payment.html', form=form, payment=payment)


@copper_bp.route('/worker/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_worker_payment(payment_id):
    from copper.models import WorkerPayment
    from core.models import PaymentReview

    payment = WorkerPayment.query.get_or_404(payment_id)
    reason = request.form.get('change_reason', '')
    if not reason or not reason.strip():
        flash('Delete reason is required.', 'danger')
        return redirect(url_for('copper.pay_worker'))

    try:
        from flask_login import current_user
        from core.models import create_notification, User
        # upsert pending review for worker delete request
        from core.models import PaymentReviewStatus
        existing = PaymentReview.query.filter_by(
            payment_id=payment.id,
            status=PaymentReviewStatus.PENDING_REVIEW.value,
        ).first()
        if existing:
            existing.mineral_type = None
            existing.type = 'Umukozi'
            existing.customer = payment.worker_name
            existing.amount = payment.amount
            existing.currency = 'RWF'
            existing.created_by_id = getattr(current_user, 'id', None)
            existing.boss_comment = (f"Delete requested: {reason.strip()}")
        else:
            review = PaymentReview(
                mineral_type=None,
                type='Umukozi',
                customer=payment.worker_name,
                amount=payment.amount,
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment=(f"Delete requested: {reason.strip()}"),
            )
            db.session.add(review)
        boss_user = User.query.filter_by(role='boss').first()
        if boss_user:
            create_notification(
                user_id=boss_user.id,
                type_='PAYMENT_DELETE_REQUEST',
                message=f"Hasabwe gusuzuma: Gusiba kwishyura umukozi - {payment.worker_name}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {reason.strip()}",
                related_type='worker_payment',
                related_id=payment.id,
            )
        db.session.commit()
        flash('Delete request submitted for boss review; payment was not deleted until approval.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error submitting delete request: {e}', 'danger')

    return redirect(url_for('copper.pay_worker'))
