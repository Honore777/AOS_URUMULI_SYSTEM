"""
Output Routes
Handles copper output/sales recording
"""
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash

from config import db
from copper.models import CopperStock, CopperOutput
from copper import copper_bp
from core.auth import role_required
from utils import calculate_unit_percentage
from flask import request


def _normalize_amount_to_rwf(amount, currency, exchange_rate):
    currency_code = (currency or 'RWF').upper()
    input_amount = float(amount or 0)
    rate = float(exchange_rate or 0)

    if currency_code == 'RWF':
        return input_amount, 1.0
    if currency_code == 'USD':
        if rate <= 0:
            raise ValueError('Exchange rate is required and must be greater than 0 for USD transactions.')
        return input_amount * rate, rate
    raise ValueError(f'Unsupported currency: {currency_code}')


@copper_bp.route("/outputs", methods=["GET", "POST"])
@role_required("accountant")
def record_output():
        """Record copper output"""
        from copper.forms import CopperOutputForm
        
        form = CopperOutputForm()
        # Populate stock choices for the dropdown (filter in DB to avoid pulling all rows)
        # Query only needed columns for the choices to avoid loading full ORM objects
        stock_rows = (
            db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.local_balance, CopperStock.supplier)
            .filter(CopperStock.local_balance > 0)
            .order_by(CopperStock.date.desc())
            .all()
        )
        form.stock_id.choices = [
            (r.id, f"{r.voucher_no} ({r.local_balance})  ({r.supplier})") for r in stock_rows
        ]

        if request.method == "POST":
            stock_id = int(request.form.get("stock_id"))
            stock = CopperStock.query.get_or_404(stock_id)
            date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
            output_kg = float(request.form.get("output_kg") or 0)
            customer = request.form.get("customer")
            output_amount = float(request.form.get('output_amount') or 0)
            amount_paid = float(request.form.get('amount_paid') or 0)
            currency = (request.form.get('currency') or 'RWF').upper()
            exchange_rate_input = request.form.get('exchange_rate')
            payment_stage = (request.form.get('payment_stage') or 'full_settlement').strip().lower()
            note = request.form.get("note")

            try:
                output_amount_rwf, exchange_rate = _normalize_amount_to_rwf(output_amount, currency, exchange_rate_input)
                amount_paid_rwf, _ = _normalize_amount_to_rwf(amount_paid, currency, exchange_rate_input)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for('copper.record_output'))

            # Use the already-fetched `stock` object instead of a second DB call
            available_balance = stock.local_balance or 0

            if output_kg > available_balance:
                flash(f"❌ Error: You cannot output {output_kg} kg. Only {available_balance} kg available.", "danger")
                return redirect(url_for('copper.record_output'))

            # Create new output record
            out = CopperOutput(
                stock_id=stock.id,
                date=date,
                output_kg=output_kg,
                output_amount=output_amount,
                output_amount_rwf=output_amount_rwf,
                amount_paid=amount_paid,
                amount_paid_rwf=amount_paid_rwf,
                currency=currency,
                exchange_rate=exchange_rate,
                payment_stage=payment_stage,
                customer=customer,
                note=note
            )

            out.update_debt()
            db.session.add(out)
            # Flush so output is visible to DB-side aggregates (remaining_stock)
            db.session.flush()

            # Recalculate the related stock's remaining local balance and apply delta to aggregate
            try:
                old_q, old_wp, old_t = CopperStock.contribution(stock)
            except Exception:
                old_q = old_wp = old_t = 0.0

            stock.local_balance = stock.remaining_stock()
            stock.unit_percent = calculate_unit_percentage(stock.local_balance, stock.percentage)
            stock.update_calculations()

            try:
                new_q, new_wp, new_t = CopperStock.contribution(stock)
                CopperStock.apply_aggregate_delta(new_q - old_q, new_wp - old_wp, new_t - old_t)
            except Exception:
                import logging
                logging.exception("record_output: failed to apply aggregate delta")

            db.session.commit()

            # --- IN-APP NOTIFICATION TO ALL ACTIVE STOREKEEPERS ---
            from core.models import create_notification, User
            storekeepers = User.query.filter_by(role='store_keeper', is_active=True).all()
            emails = []
            for sk in storekeepers:
                create_notification(
                    user_id=sk.id,
                    type_='OUTPUT_CREATED',
                    message=f"Stock output of {output_kg} kg for {stock.voucher_no} requires your processing.",
                    related_type='output',
                    related_id=out.id
                )
                if getattr(sk, 'email', None):
                    emails.append(sk.email)

            # Persist notifications before attempting email
            db.session.commit()

            # --- EMAIL NOTIFICATION TO STOREKEEPERS (Brevo) ---
            from flask import current_app
            from flask_login import current_user
            from utils import send_brevo_email_async
            output_details = f"Stock: {stock.voucher_no}, Supplier: {stock.supplier}, Output: {output_kg} kg, Note: {note}"
            subject = "Stock Output Request"
            html_content = (
                "<p>Dear Storekeeper,</p>"
                f"<p>Accountant {getattr(current_user, 'name', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) yasabye gusohora izi stock zikurikira:</p>"
                f"<p>{output_details}</p>"
                "<p>Jya muri sisiteme urebe neza stock uribuze gusohora.</p>"
                "<p>Regards,<br>Urumuli Smart System</p>"
            )
            try:
                recipient_list = emails if emails else ["storekeeper@example.com"]
                send_brevo_email_async(subject, html_content, recipient_list)
            except Exception:
                import logging
                logging.exception("Failed to enqueue copper output email via Brevo")
                flash("Email notification failed; in-app notification(s) saved.", "warning")
            


            flash(f"Output recorded ({output_kg} kg) for {stock.voucher_no}. Customer and amount will be added later by the negotiator.", "success")
            return redirect(url_for("copper.record_output"))

        customer_filter = request.args.get('customer') or ''
        batch_filter = (request.args.get('batch_id') or '').strip()
        date_from = request.args.get('from') or ''
        date_to = request.args.get('to') or ''

        q = CopperOutput.query
        if customer_filter:
            q = q.filter(CopperOutput.customer == customer_filter)
        if batch_filter:
            q = q.filter(CopperOutput.batch_id == batch_filter)
        # parse dates (YYYY-MM-DD) defensively
        try:
            if date_from:
                d1 = datetime.strptime(date_from, '%Y-%m-%d').date()
                q = q.filter(CopperOutput.date >= d1)
            if date_to:
                d2 = datetime.strptime(date_to, '%Y-%m-%d').date()
                q = q.filter(CopperOutput.date <= d2)
        except Exception:
            # ignore parse errors and show unfiltered results
            pass

        outputs = q.order_by(CopperOutput.date.desc()).limit(200).all()

        batch_summaries = []
        single_outputs = []
        if not batch_filter:
            batches = {}
            for out in outputs:
                bid = (out.batch_id or '').strip()
                if not bid:
                    single_outputs.append(out)
                    continue
                if bid not in batches:
                    batches[bid] = {
                        'batch_id': bid,
                        'customer': out.customer,
                        'first_date': out.date,
                        'last_date': out.date,
                        'total_kg': 0.0,
                        'total_paid': 0.0,
                        'total_debt': 0.0,
                        'count': 0,
                    }
                b = batches[bid]
                if out.date and (not b['first_date'] or out.date < b['first_date']):
                    b['first_date'] = out.date
                if out.date and (not b['last_date'] or out.date > b['last_date']):
                    b['last_date'] = out.date
                b['customer'] = b['customer'] or out.customer
                b['total_kg'] += float(out.output_kg or 0.0)
                b['total_paid'] += float(out.amount_paid_rwf or out.amount_paid or 0.0)
                b['total_debt'] += float(out.debt_remaining or 0.0)
                b['count'] += 1

            batch_summaries = sorted(
                list(batches.values()),
                key=lambda r: (r.get('last_date') or datetime.utcnow().date()),
                reverse=True,
            )

        return render_template(
            "copper/outputs.html",
            outputs=outputs,
            form=form,
            customer_filter=customer_filter,
            batch_filter=batch_filter,
            batch_summaries=batch_summaries,
            single_outputs=single_outputs,
            date_from=date_from,
            date_to=date_to,
        )
