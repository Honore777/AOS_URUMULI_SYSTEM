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
    achieved_total_quantity = 0
    mode = None
    all_stocks = []
    # If user arrived via a plain GET (no explicit mode param), avoid
    # re-entering a previously-stored edit mode. Only restore edit mode
    # when the user explicitly requests it (mode=edit in query string).
    # Also clear prior optimization state so clicking Back -> Dashboard
    # then Optimize opens the initial target-entry step.
    # Treat explicit 'None' (string) or empty values as absent — in those
    # cases we should clear previous optimization state so a plain
    # Optimize click always shows the initial target-entry view.
    mode_arg = request.args.get('mode')
    if request.method == 'GET' and (mode_arg is None or (isinstance(mode_arg, str) and mode_arg.strip().lower() in ('', 'none'))):
        for _k in (
            'optimization_mode',
            'optimization_quantities',
            'optimization_target_moyenne',
            'optimization_target_moyenne_nb',
            'optimization_target_total_quantity',
            'cassiterite_optimization_edits',
        ):
            session.pop(_k, None)
    
    if form.validate_on_submit():
        target_moyenne = form.target_moyenne.data
        action = request.form.get('action', '')
        
        # ═══════════════════════════════════════════════════
        # STEP 1: User clicks "Filter Stocks" with target
        # ═══════════════════════════════════════════════════
        if action == 'filter':
            selected_stocks, achieved_moyenne, achieved_total_quantity = select_stocks_for_average_quality(
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
            selected_stocks, achieved_moyenne, achieved_total_quantity = select_stocks_for_average_quality(
                target_moyenne=target_moyenne,
                minimize_quantity=True,
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
            # Merge any page-saved edits from session with current form inputs
            session_edits = session.get('cassiterite_optimization_edits', {}) or {}
            merged_edits = {}
            for k, v in (session_edits or {}).items():
                try:
                    merged_edits[int(k)] = float(v)
                except Exception:
                    continue

            # Fetch all candidate stocks once to validate and clamp
            all_stocks_list = db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance).filter(CassiteriteStock.local_balance > 0).order_by(CassiteriteStock.date.desc()).all()

            # Seed minimum_quantities from previously computed recommended quantities
            # so Recalculate starts from the recommended baseline and then applies edits.
            recommended = session.get('optimization_quantities', {}) or {}
            seeded_minima = {}
            for k, v in recommended.items():
                try:
                    seeded_minima[int(k)] = float(v)
                except Exception:
                    continue
            minimum_quantities.update(seeded_minima)

            for s in all_stocks_list:
                qty_key = f'qty_{s.id}'
                # Form input overrides any session-saved edit
                form_val = None
                if qty_key in request.form:
                    try:
                        user_qty = request.form[qty_key].strip()
                        if user_qty:
                            form_val = float(user_qty)
                    except (ValueError, TypeError):
                        form_val = None

                if form_val is not None:
                    candidate = min(form_val, s.local_balance)
                    merged_edits[s.id] = candidate

            # Build/overlay minimum_quantities from merged_edits when user changed from baseline
            for s in all_stocks_list:
                if s.id in merged_edits:
                    min_qty = merged_edits[s.id]
                    # Clamp to available
                    if min_qty < 0:
                        min_qty = 0.0
                    if min_qty > s.local_balance:
                        min_qty = float(s.local_balance)

                    baseline = seeded_minima.get(s.id, s.local_balance)
                    if abs(min_qty - baseline) > 0.01:
                        minimum_quantities[s.id] = min_qty

            # Persist merged edits back to session for continuity
            session['cassiterite_optimization_edits'] = {str(k): float(v) for k, v in merged_edits.items()}

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
            selected_stocks, achieved_moyenne, achieved_total_quantity = select_stocks_for_average_quality(
                target_moyenne=target_moyenne
            )
            quantities = {s.id: s.local_balance for s in selected_stocks}
    
        # If the user performed a GET search while in edit mode (mode=edit),
        # rehydrate the previously computed recommendation from session so the
        # supplier search / pagination keeps the user in the edit view instead
        # of dropping back to the initial page.
        if request.method == 'GET' and request.args.get('mode') == 'edit':
            try:
                sess_qty = session.get('optimization_quantities') or {}
                if sess_qty:
                    ids = [int(k) for k in sess_qty.keys()]
                    selected_stocks = CassiteriteStock.query.filter(CassiteriteStock.id.in_(ids)).all()
                    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
                    mode = 'edit'
                else:
                    # Fall back to recomputing selection using provided or stored target
                    tgt = request.args.get('target_moyenne') or session.get('optimization_target_moyenne')
                    try:
                        tgt_f = float(tgt) if tgt not in (None, '') else None
                    except Exception:
                        tgt_f = None
                    if tgt_f is not None:
                        selected_stocks, achieved_moyenne, achieved_total_quantity = select_stocks_for_average_quality(target_moyenne=tgt_f, minimize_quantity=True)
                        quantities = {s.id: s.local_balance for s in selected_stocks}
                        mode = 'edit'
            except Exception:
                selected_stocks = []
                quantities = {}
                mode = None

        # Get all stocks for edit mode display (only selected columns)
        # Support pagination and supplier search for edit table
    try:
        page = int(request.args.get('page', 1))
        if page < 1:
            page = 1
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get('per_page', 40))
        if per_page < 5:
            per_page = 5
        if per_page > 200:
            per_page = 200
    except Exception:
        per_page = 40

    q = (request.args.get('q') or '').strip()
    base_q = db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance).filter(CassiteriteStock.local_balance > 0)
    if q:
        base_q = base_q.filter(CassiteriteStock.supplier.ilike(f"%{q}%"))

    total_count = base_q.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    all_stocks = base_q.order_by(CassiteriteStock.date.desc()).offset((page - 1) * per_page).limit(per_page).all()
    
    # Only persist session values when appropriate to avoid wiping
    # previously computed state on unrelated GETs (e.g., supplier search).
    def _set_session_target_if_present(key, value):
        if value is None:
            return
        if isinstance(value, str) and value.strip().lower() in ('', 'none'):
            return
        try:
            session[key] = float(value)
        except Exception:
            session[key] = value

    if quantities:
        session['optimization_quantities'] = quantities

    if mode is not None:
        session['optimization_mode'] = mode

    # Prefer POSTed value after a Filter/Edit action; otherwise pick up
    # explicit query params when present (pagination/search links).
    if request.method == 'POST' and form is not None:
        _set_session_target_if_present('optimization_target_moyenne', form.target_moyenne.data)
    else:
        _set_session_target_if_present('optimization_target_moyenne', request.args.get('target_moyenne'))
    
    return render_template(
        'cassiterite/optimize.html',
        selected_stocks=selected_stocks,
        all_stocks=all_stocks,
        achieved_moyenne=achieved_moyenne,
        achieved_total_quantity=achieved_total_quantity,
        quantities=quantities,
        mode=mode,
        form=form,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
        q=q,
    )


@cassiterite_bp.route('/optimize/totals', methods=['GET'])
def cassiterite_optimize_totals():
    """Return JSON with server-authoritative totals for the current cassiterite optimization session."""
    try:
        quantities = session.get('optimization_quantities', {}) or {}
        # If session empty, try to recompute using stored target
        if not quantities:
            tgt = session.get('optimization_target_moyenne')

            def _has_valid_target(val):
                if val is None:
                    return False
                if isinstance(val, str) and val.strip().lower() in ('', 'none'):
                    return False
                return True

            if _has_valid_target(tgt):
                try:
                    selected_stocks, achieved, _achieved_total = select_stocks_for_average_quality(target_moyenne=tgt)
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


@cassiterite_bp.route('/optimize/save_edits', methods=['POST'])
def cassiterite_save_edits():
    """AJAX endpoint to persist partial cassiterite edits (id -> qty) into session."""
    try:
        payload = request.get_json() or {}
        edits = session.get('cassiterite_optimization_edits', {}) or {}
        for sid, qty in (payload.get('edits') or {}).items():
            try:
                sid_i = int(sid)
                qty_f = float(qty)
                if qty_f < 0:
                    continue
                edits[str(sid_i)] = qty_f
            except Exception:
                continue
        session['cassiterite_optimization_edits'] = edits
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False}), 400


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
