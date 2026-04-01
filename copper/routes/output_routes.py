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
            note = request.form.get("note")

            stock1 = CopperStock.query.get(stock_id)
            available_balance = stock1.local_balance or 0

            if output_kg > available_balance:
                flash(f"❌ Error: You cannot output {output_kg} kg. Only {available_balance} kg available.", "danger")
                return redirect(url_for('copper.record_output'))

            # Create new output record
            out = CopperOutput(
                stock_id=stock.id,
                date=date,
                output_kg=output_kg,
                output_amount=output_amount,
                amount_paid=amount_paid,
                customer=customer,
                note=note
            )

            out.update_debt()
            db.session.add(out)
            # Flush so output is visible to DB-side aggregates (remaining_stock)
            db.session.flush()

            # Recalculate the related stock's remaining local balance and aggregates
            stock1.local_balance = stock1.remaining_stock()
            stock1.unit_percent = calculate_unit_percentage(stock1.local_balance, stock1.percentage)
            from copper.models import CopperStock
            CopperStock.update_global_moyennes()

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
            output_details = f"Stock: {stock.voucher_no}, Supplier: {stock.supplier}, Output: {output_kg} kg, Customer: {customer}, Note: {note}"
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
            


            flash(f"Output recorded ({output_kg} kg) for {stock.voucher_no}. Moyenne and Moyenne NB updated.", "success")
            return redirect(url_for("copper.record_output"))

        # Server-side filters (via GET): customer, from, to
        customer_filter = request.args.get('customer') or ''
        date_from = request.args.get('from') or ''
        date_to = request.args.get('to') or ''

        q = CopperOutput.query
        if customer_filter:
            q = q.filter(CopperOutput.customer == customer_filter)
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

        outputs = q.order_by(CopperOutput.date.desc()).limit(60).all()
        return render_template("copper/outputs.html", outputs=outputs, form=form,
                               customer_filter=customer_filter, date_from=date_from, date_to=date_to)
