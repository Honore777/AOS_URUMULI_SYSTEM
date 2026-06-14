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
            .filter(CopperStock.local_balance > 0, CopperStock.is_deleted.is_(False))
            .order_by(CopperStock.date.desc())
            .all()
        )
        form.stock_id.choices = [
            (r.id, f"{r.voucher_no} ({r.local_balance})  ({r.supplier})") for r in stock_rows
        ]

        if request.method == "POST":
            stock_id = int(request.form.get("stock_id"))
            stock = CopperStock.query.get_or_404(stock_id)
            if getattr(stock, "is_deleted", False):
                flash("Selected stock is deleted. Choose another voucher.", "danger")
                return redirect(url_for("copper.record_output"))
            date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
            output_kg = float(request.form.get("output_kg") or 0)
            customer = (request.form.get("customer") or '').strip()
            output_amount = float(request.form.get('output_amount') or 0)

            # Guard: prevent accidental near-duplicate customer identities.
            if customer:
                try:
                    existing_names = [r[0] for r in db.session.query(CopperOutput.customer).filter(CopperOutput.customer.isnot(None), CopperOutput.customer != '').distinct().all()]
                except Exception:
                    existing_names = []
                norm_new = normalize_counterparty_name(customer)
                exact_exists = any(normalize_counterparty_name(n) == norm_new for n in existing_names)
                if not exact_exists:
                    close = close_name_matches(customer, existing_names, limit=5, cutoff=0.86)
                    if close:
                        flash(
                            f"Customer name looks similar to existing customer(s): {', '.join(close[:3])}. Consider using the existing name to avoid duplication.",
                            'warning',
                        )
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

        q = CopperOutput.query.filter(CopperOutput.is_deleted.is_(False))
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

        # Fetch BulkOutputPlan data for batches to get weight and profit/loss info
        from core.models import BulkOutputPlan, BatchDeduction
        batch_plans = {}
        if not batch_filter:
            # Get all unique batch_ids from outputs
            batch_ids = set(out.batch_id for out in outputs if out.batch_id)
            if batch_ids:
                plans = BulkOutputPlan.query.filter(
                    BulkOutputPlan.batch_id.in_(batch_ids),
                    BulkOutputPlan.mineral_type.in_(['copper', 'coltan'])
                ).all()
                for plan in plans:
                    batch_plans[plan.batch_id] = plan

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
                    plan = batch_plans.get(bid)
                    batches[bid] = {
                        'batch_id': bid,
                        'customer': out.customer,
                        'first_date': out.date,
                        'last_date': out.date,
                        'total_kg': 0.0,
                        'total_paid': 0.0,
                        'total_debt': 0.0,
                        'count': 0,
                        'gross_weight': float(plan.gross_weight or 0) if plan else 0,
                        'tare_weight': float(plan.tare_weight or 0) if plan else 0,
                        'moisture_percent': float(plan.moisture_percent or 0) if plan else 0,
                        'moisture_weight': float(plan.moisture_weight or 0) if plan else 0,
                        'net_weight': float(plan.net_weight or 0) if plan else 0,
                        'sample_weight': float(plan.sample_weight or 0) if plan else 0,
                        'final_weight': float(plan.final_weight or 0) if plan else 0,
                        'agreed_amount': float(plan.total_expected_amount or 0) if plan else 0,
                        'agreed_amount_rwf': float(plan.total_expected_amount or 0) * (float(plan.exchange_rate or 1.0) if plan and plan.currency == 'USD' else 1.0) if plan else 0,
                        'currency': plan.currency if plan else 'RWF',
                        'exchange_rate': float(plan.exchange_rate or 1.0) if plan else 1.0,
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

            # Calculate COGS and profit/loss for each batch
            for batch_id, b in batches.items():
                plan = batch_plans.get(batch_id)
                if plan and plan.plan_json:
                    # Calculate COGS using proportional allocation
                    cogs_rwf = 0.0
                    for item in plan.plan_json:
                        stock_id = item.get('stock_id')
                        planned_kg = float(item.get('planned_output_kg') or 0)
                        if stock_id and planned_kg > 0:
                            stock = CopperStock.query.get(stock_id)
                            if stock and stock.input_kg and stock.input_kg > 0:
                                # Proportional COGS: (planned_kg / input_kg) * net_balance
                                proportion = planned_kg / stock.input_kg
                                cogs_rwf += proportion * float(stock.net_balance or 0)
                    b['cogs_rwf'] = cogs_rwf

                    # Calculate deductions (BatchDeduction.batch_id is Integer FK to plan.id)
                    deductions = BatchDeduction.query.filter(
                        BatchDeduction.batch_id == plan.id
                    ).all()
                    total_deductions = float(sum(d.amount_rwf or 0 for d in deductions))
                    b['total_deductions'] = total_deductions

                    # Convert agreed amount to RWF for calculation
                    agreed_rwf = b['agreed_amount']
                    if b['currency'] == 'USD' and b['exchange_rate'] > 0:
                        agreed_rwf = b['agreed_amount'] * b['exchange_rate']

                    # Net Sales = Agreed - Deductions (what we actually received)
                    b['net_sales_rwf'] = agreed_rwf - total_deductions

                    # Profit/Loss = Net Sales - COGS
                    b['profit_loss_rwf'] = b['net_sales_rwf'] - cogs_rwf
                else:
                    b['cogs_rwf'] = 0.0
                    b['total_deductions'] = 0.0
                    b['net_sales_rwf'] = 0.0
                    b['profit_loss_rwf'] = 0.0

            batch_summaries = sorted(
                list(batches.values()),
                key=lambda r: (r.get('last_date') or datetime.utcnow().date()),
                reverse=True,
            )

        # Build stock composition for batch detail view
        batch_plan_details = None
        if batch_filter:
            plan = BulkOutputPlan.query.filter(
                BulkOutputPlan.batch_id == batch_filter,
                BulkOutputPlan.mineral_type.in_(['copper', 'coltan'])
            ).first()
            if plan and plan.plan_json:
                stock_items = []
                total_planned_kg = 0.0
                weighted_percentage = 0.0
                weighted_nb = 0.0
                for item in plan.plan_json:
                    stock_id = item.get('stock_id')
                    planned_kg = float(item.get('planned_output_kg') or 0)
                    stock = CopperStock.query.get(stock_id) if stock_id else None
                    if stock:
                        stock_items.append({
                            'stock_id': stock_id,
                            'voucher_no': stock.voucher_no,
                            'supplier': stock.supplier,
                            'percentage': float(stock.percentage or 0),
                            'nobelium': float(stock.nb or 0) if stock.nb is not None else None,
                            'input_kg': float(stock.input_kg or 0),
                            'planned_output_kg': planned_kg,
                        })
                        total_planned_kg += planned_kg
                        weighted_percentage += planned_kg * float(stock.percentage or 0)
                        if stock.nb is not None:
                            weighted_nb += planned_kg * float(stock.nb)
                batch_moyenne = (weighted_percentage / total_planned_kg) if total_planned_kg > 0 else 0
                batch_moyenne_nb = (weighted_nb / total_planned_kg) if total_planned_kg > 0 else 0

                # Fetch individual deduction line items for this batch
                deduction_rows = BatchDeduction.query.filter(
                    BatchDeduction.batch_id == plan.id
                ).order_by(BatchDeduction.created_at.asc()).all()
                deductions_list = []
                for d in deduction_rows:
                    deductions_list.append({
                        'deduction_type': d.deduction_type,
                        'amount_input': float(d.amount_input or 0),
                        'currency': d.currency or 'RWF',
                        'exchange_rate': float(d.exchange_rate or 1.0),
                        'amount_rwf': float(d.amount_rwf or 0),
                    })

                batch_plan_details = {
                    'plan': plan,
                    'stock_items': stock_items,
                    'batch_moyenne': round(batch_moyenne, 2),
                    'batch_moyenne_nb': round(batch_moyenne_nb, 2) if batch_moyenne_nb > 0 else None,
                    'total_planned_kg': round(total_planned_kg, 2),
                    'deductions': deductions_list,
                }

        return render_template(
            "copper/outputs.html",
            outputs=outputs,
            form=form,
            customer_filter=customer_filter,
            batch_filter=batch_filter,
            batch_summaries=batch_summaries,
            single_outputs=single_outputs,
            batch_plan_details=batch_plan_details,
            date_from=date_from,
            date_to=date_to,
        )
