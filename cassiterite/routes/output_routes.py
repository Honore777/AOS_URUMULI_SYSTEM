"""
Cassiterite Output Routes - THREE-STEP Optimization Process
STEP 1 (mode="initial"): User enters target moyenne → Filter stocks (BINARY)
STEP 2 (mode="edit"): User clicks "Edit Selection" → Adjust quantities manually
STEP 3 (mode="result"): User clicks "Recalculate" → Hybrid optimization
"""
from flask import render_template, request, redirect, url_for, flash, session, jsonify
from config import db
from cassiterite.models import CassiteriteStock, CassiteriteOutput
from cassiterite.forms import RecordCassiteriteOutputForm, OptimizeCassiteriteForm
from cassiterite.routes import cassiterite_bp
from cassiterite_optimization import select_stocks_for_average_quality, select_stocks_with_minimum_quantities_cassiterite
from core.auth import role_required
from core.models import BulkOutputPlan, BulkPlanStatus, User, create_notification
from datetime import datetime
from uuid import uuid4
from flask_login import current_user


@cassiterite_bp.route('/record_output', methods=['GET', 'POST'])
@role_required("accountant")
def record_output():
    """Record cassiterite output (single)"""
    form = RecordCassiteriteOutputForm()
    
    # Populate stock choices
    # Populate choices by selecting only required columns to avoid loading full ORM objects
    stock_rows = (
        db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance)
        .filter(CassiteriteStock.local_balance > 0)
        .order_by(CassiteriteStock.date.desc())
        .all()
    )
    form.stock_id.choices = [(r.id, f"{r.voucher_no} - {r.supplier} - ({r.local_balance}kg)") for r in stock_rows]
    
    if form.validate_on_submit():
        stock = CassiteriteStock.query.get(form.stock_id.data)
        
        if not stock:
            flash("Stock not found!", "error")
            return redirect(url_for('cassiterite.record_output'))
        
        # Create output
        output = CassiteriteOutput(
            stock_id=stock.id,
            date=form.date.data,
            output_kg=form.output_kg.data,
            customer=form.customer.data,
            output_amount=form.output_amount.data,
            amount_paid=form.amount_paid.data if hasattr(form, 'amount_paid') else 0,
            note=form.note.data,
            voucher_no=stock.voucher_no
        )

        # Ensure debt_remaining is correctly calculated
        output.update_debt()
        db.session.add(output)
        db.session.flush()
        
        # Update stock local balance and recalculate
        stock.local_balance = stock.remaining_stock()
        stock.unit_percent = (stock.local_balance * stock.percentage) / 100 if stock.percentage else 0
        CassiteriteStock.update_global_moyennes()
        
        db.session.commit()

        # --- IN-APP NOTIFICATION TO ALL ACTIVE STOREKEEPERS ---
        from core.models import create_notification, User
        storekeepers = User.query.filter_by(role='store_keeper', is_active=True).all()
        emails = []
        for sk in storekeepers:
            create_notification(
                user_id=sk.id,
                type_='OUTPUT_CREATED',
                message=f"Cassiterite stock output of {form.output_kg.data} kg for {stock.voucher_no} requires your processing.",
                related_type='cassiterite_output',
                related_id=output.id
            )
            if getattr(sk, 'email', None):
                emails.append(sk.email)

        # Persist notifications before attempting email
        db.session.commit()

        # --- EMAIL NOTIFICATION TO STOREKEEPERS (Brevo) ---
        from flask import current_app
        from flask_login import current_user
        from utils import send_brevo_email_async
        output_details = f"Stock: {stock.voucher_no}, Supplier: {stock.supplier}, Output: {form.output_kg.data} kg, Customer: {form.customer.data}, Note: {form.note.data}"
        subject = "Cassiterite Stock Output Request"
        html_content = (
            "<p>Dear Storekeeper,</p>"
            f"<p>Accountant {getattr(current_user, 'name', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')})Yasabye gusohora iyi stock ikurikira ya Gasegereti:</p>"
            f"<p>{output_details}</p>"
            "<p>Jya muri sisitemu kureba neza stock uribuze gusohora.</p>"
            "<p>Regards,<br> Urumuli Smart System</p>"
        )
        try:
            recipient_list = emails if emails else ["storekeeper@example.com"]
            send_brevo_email_async(subject, html_content, recipient_list)
        except Exception:
            import logging
            logging.exception("Failed to enqueue cassiterite output email via Brevo")
            flash("Email notification failed; in-app notification(s) saved.", "warning")

        flash(f"Output of {form.output_kg.data}kg recorded!", "success")
        return redirect(url_for('cassiterite.list_outputs'))
    
    return render_template('cassiterite/record_output.html', form=form)


@cassiterite_bp.route('/optimize', methods=['GET', 'POST'])
@role_required("accountant")
def optimize():
    """
    THREE-STEP Optimization Process for Cassiterite
    
    STEP 1 (mode="initial"): User enters target moyenne → Auto-filter stocks (BINARY)
    STEP 2 (mode="edit"): User clicks "Edit Selection" → Adjust quantities
    STEP 3 (mode="result"): User clicks "Recalculate" → Hybrid optimization
    """
    form = OptimizeCassiteriteForm()
    selected_stocks = []
    achieved_moyenne = 0
    quantities = {}
    mode = None
    all_stocks = []
    
    if form.validate_on_submit():
        target_moyenne = form.target_moyenne.data
        action = request.form.get('action', '')
        
        # ═══════════════════════════════════════════════════
        # STEP 1: User clicks "Filter Stocks" with target
        # ═══════════════════════════════════════════════════
        if action == 'filter':
            selected_stocks, achieved_moyenne = select_stocks_for_average_quality(
                target_moyenne=target_moyenne
            )
            
            # Create quantity dict (show full available amount as recommended)
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'initial'
            
            if selected_stocks:
                flash(f"✓ Found {len(selected_stocks)} stocks matching target moyenne {target_moyenne}%", "success")
            else:
                flash("No stocks found for target moyenne!", "warning")
        
        # ═══════════════════════════════════════════════════
        # STEP 2: User clicks "Edit Selection"
        # ═══════════════════════════════════════════════════
        elif action == 'edit':
            # Get the previously selected stocks for reference
            selected_stocks, achieved_moyenne = select_stocks_for_average_quality(
                target_moyenne=target_moyenne
            )
            
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'edit'
            # For edit mode display, fetch only required columns
            all_stocks = db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance).filter(CassiteriteStock.local_balance > 0).order_by(CassiteriteStock.date.desc()).all()
        
        # ═══════════════════════════════════════════════════
        # STEP 3: User clicks "Recalculate" with adjustments
        # ═══════════════════════════════════════════════════
        elif action == 'recalculate':
            # Capture user's adjusted quantities
            minimum_quantities = {}
            all_stocks_list = db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance).filter(CassiteriteStock.local_balance > 0).order_by(CassiteriteStock.date.desc()).all()
            
            for s in all_stocks_list:
                qty_key = f'qty_{s.id}'
                if qty_key in request.form:
                    try:
                        user_qty = request.form[qty_key].strip()
                        if user_qty:
                            min_qty = float(user_qty)
                            min_qty = min(min_qty, s.local_balance)  # Cap to available
                            
                            # Only add if user changed from full balance
                            if abs(min_qty - s.local_balance) > 0.01:  # float tolerance
                                if min_qty > 0:
                                    minimum_quantities[s.id] = min_qty
                    except (ValueError, TypeError):
                        pass
            
            # Re-optimize with hybrid variables
            selected_stocks, achieved_moyenne, quantities = select_stocks_with_minimum_quantities_cassiterite(
                target_moyenne=target_moyenne,
                minimum_quantities=minimum_quantities
            )
            mode = 'result'
        
        # ═══════════════════════════════════════════════════
        # Back: Return to initial
        # ═══════════════════════════════════════════════════
        elif action == 'back_to_initial':
            mode = 'initial'
            selected_stocks, achieved_moyenne = select_stocks_for_average_quality(
                target_moyenne=target_moyenne
            )
            quantities = {s.id: s.local_balance for s in selected_stocks}
    
    # Get all stocks for edit mode display (only selected columns)
    all_stocks = db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance).filter(CassiteriteStock.local_balance > 0).order_by(CassiteriteStock.date.desc()).all()
    
    # Store in session
    session['optimization_quantities'] = quantities
    session['optimization_mode'] = mode
    session['optimization_target_moyenne'] = form.target_moyenne.data if form.target_moyenne.data else 0
    
    return render_template(
        'cassiterite/optimize.html',
        selected_stocks=selected_stocks,
        all_stocks=all_stocks,
        achieved_moyenne=achieved_moyenne,
        quantities=quantities,
        mode=mode,
        form=form
    )


@cassiterite_bp.route('/optimize/totals', methods=['GET'])
def cassiterite_optimize_totals():
    """Return JSON with server-authoritative totals for the current cassiterite optimization session."""
    try:
        quantities = session.get('optimization_quantities', {}) or {}
        # If session empty, try to recompute using stored target
        if not quantities:
            tgt = session.get('optimization_target_moyenne')
            if tgt is not None:
                try:
                    selected_stocks, achieved = select_stocks_for_average_quality(target_moyenne=tgt)
                    quantities = {s.id: s.local_balance for s in selected_stocks}
                    session['optimization_quantities'] = quantities
                except Exception:
                    quantities = {}

        total = 0.0
        for v in quantities.values():
            try:
                total += float(v)
            except Exception:
                continue
        return jsonify({'total_recommended': total, 'quantities': quantities})
    except Exception:
        return jsonify({'total_recommended': 0.0, 'quantities': {}})


@cassiterite_bp.route('/confirm_bulk_output', methods=['POST'])
@role_required("accountant")
def confirm_bulk_output():
    """Record bulk cassiterite output from optimization results"""
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    customer = request.form.get("customer")
    output_amount = float(request.form.get('output_amount') or 0)
    amount_paid = float(request.form.get('amount_paid') or 0)
    note = request.form.get("note") or "Bulk output from optimization"
    
    # Get quantities from session
    quantities = session.get('optimization_quantities', {})
    
    if not quantities:
        flash("No quantities to output", "danger")
        return redirect(url_for('cassiterite.optimize'))
    
    try:
        # Calculate total quantity
        total_qty = sum(float(qty) for qty in quantities.values())
        
        if total_qty == 0:
            flash("Total quantity is zero!", "error")
            return redirect(url_for('cassiterite.optimize'))
        
        # Generate batch_id (similar format as copper)
        hex_code = uuid4().hex[:6]
        date_str = date.strftime('%Y%m%d')
        customer_safe = (customer or 'customer').lower().replace(' ', '_')[:20]
        batch_id = f"{customer_safe}_{date_str}_{hex_code}"

        # Get all cassiterite stocks
        # Only fetch stocks referenced in the quantities dict to avoid loading full table
        stock_ids = [int(k) for k in quantities.keys() if str(k).isdigit()]
        all_stocks = {s.id: s for s in CassiteriteStock.query.filter(CassiteriteStock.id.in_(stock_ids)).all()} if stock_ids else {}

        # ------------------------------------------------------------------
        # 1) Build and store a BulkOutputPlan so the store keeper (and boss)
        #    can see the exact optimal table that was used here.
        # ------------------------------------------------------------------
        plan_items = []
        for stock_id_str, qty in quantities.items():
            try:
                stock_id = int(stock_id_str)
                qty_float = float(qty)
            except (ValueError, TypeError):
                continue

            stock = all_stocks.get(stock_id)
            if not stock or qty_float <= 0:
                continue

            plan_items.append({
                "stock_id": stock.id,
                "voucher_no": stock.voucher_no,
                "supplier": stock.supplier,
                "planned_output_kg": float(qty_float),
            })

        plan = BulkOutputPlan(
            mineral_type="cassiterite",
            created_by_id=getattr(current_user, "id", None),
            status=BulkPlanStatus.SENT_TO_STORE.value,
            customer=customer,
            batch_id=batch_id,
            note=note,
            plan_json=plan_items,
        )
        db.session.add(plan)
        db.session.flush()  # ensure plan.id is available

        # Notify all active store keepers about this new cassiterite plan
        store_keepers = User.query.filter_by(role="store_keeper", is_active=True).all()
        emails = []
        for sk in store_keepers:
            create_notification(
                user_id=sk.id,
                type_="BULK_PLAN_CREATED",
                message=(
                    f"New cassiterite bulk output plan {plan.id} for customer {customer} "
                    f"(batch {batch_id})"
                ),
                related_type="bulk_plan",
                related_id=plan.id,
            )
            if getattr(sk, 'email', None):
                emails.append(sk.email)

        # Persist plan + notifications so storekeepers can see them immediately
        db.session.commit()

        # Send one email to all storekeepers with the bulk plan details
        try:
            from flask_login import current_user
            from utils import send_brevo_email_async
            subject = f"Bulk Output Plan {plan.id} - Action Required"
            html_content = (
                f"<p>Dear Storekeeper,</p>"
                f"<p>A new bulk output plan (ID: {plan.id}, batch: {batch_id}) was created by {getattr(current_user, 'name', 'Unknown')} for customer {customer}.</p>"
                f"<p>Note: {note}</p>"
                f"<p>Log into the system to review and execute the plan.</p>"
                "<p>Regards,<br>Urumuli Smart System</p>"
            )
            recipient_list = emails if emails else ["storekeeper@example.com"]
            send_brevo_email_async(subject, html_content, recipient_list)
        except Exception:
            import logging
            logging.exception("Failed to enqueue cassiterite bulk plan email via Brevo")

        # ------------------------------------------------------------------
        # 2) Execute outputs with proportional amounts (existing logic)
        # ------------------------------------------------------------------
        for stock_id_str, qty in quantities.items():
            stock_id = int(stock_id_str)
            qty = float(qty)
            
            stock = all_stocks.get(stock_id)
            if not stock:
                continue
            
            if qty > stock.local_balance:
                flash(f"Stock {stock.voucher_no}: Cannot output {qty}kg, only {stock.local_balance}kg available", "warning")
                continue
            
            # Calculate proportional amounts
            proportion = qty / total_qty if total_qty > 0 else 0
            proportional_amount = output_amount * proportion
            proportional_paid = amount_paid * proportion
            
            output = CassiteriteOutput(
                stock_id=stock.id,
                date=date,
                output_kg=qty,
                customer=customer,
                output_amount=proportional_amount,
                amount_paid=proportional_paid,
                voucher_no=stock.voucher_no,
                batch_id=batch_id,
                note=note
            )

            # Calculate remaining debt for this proportional line
            output.update_debt()
            db.session.add(output)
            
            # Update stock
            stock.local_balance = stock.remaining_stock()
            stock.unit_percent = (stock.local_balance * stock.percentage) / 100 if stock.percentage else 0
        
        # Mark the bulk plan as executed and record who executed it
        plan.status = BulkPlanStatus.EXECUTED.value
        plan.executed_at = datetime.utcnow()
        plan.executed_by_id = getattr(current_user, "id", None)

        CassiteriteStock.update_global_moyennes()
        db.session.commit()
        
        # Clean session
        session.pop('optimization_quantities', None)
        session.pop('optimization_mode', None)
        session.pop('optimization_target_moyenne', None)
        
        flash(f"✓ Batch {batch_id} recorded successfully!", "success")
        return redirect(url_for('cassiterite.list_outputs'))
    
    except Exception as e:
        db.session.rollback()
        flash(f"Error recording batch output: {str(e)}", "error")
        return redirect(url_for('cassiterite.optimize'))


@cassiterite_bp.route('/outputs')
def list_outputs():
    """List all cassiterite outputs"""
    # Support simple GET filters: customer, from, to - apply before limiting
    from flask import request
    customer_filter = request.args.get('customer') or ''
    date_from = request.args.get('from') or ''
    date_to = request.args.get('to') or ''

    q = CassiteriteOutput.query
    if customer_filter:
        q = q.filter(CassiteriteOutput.customer == customer_filter)
    from datetime import datetime
    try:
        if date_from:
            d1 = datetime.strptime(date_from, '%Y-%m-%d').date()
            q = q.filter(CassiteriteOutput.date >= d1)
        if date_to:
            d2 = datetime.strptime(date_to, '%Y-%m-%d').date()
            q = q.filter(CassiteriteOutput.date <= d2)
    except Exception:
        pass

    outputs = q.order_by(CassiteriteOutput.date.desc()).limit(60).all()
    return render_template('cassiterite/outputs.html', outputs=outputs,
                           customer_filter=customer_filter, date_from=date_from, date_to=date_to)
