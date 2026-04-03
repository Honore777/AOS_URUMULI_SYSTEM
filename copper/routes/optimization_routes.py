"""
Optimization Routes
Handles copper stock optimization with three-step process:
STEP 1 (mode="initial"): User enters targets → System filters recommended stocks
STEP 2 (mode="edit"): User clicks "Edit Selection" → Shows ALL stocks for editing
STEP 3 (mode="result"): User adjusts quantities → System re-optimizes with constraints
"""
from flask import render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime
import json
import uuid
from copper.models import CopperStock, CopperOutput
from core.models import BulkOutputPlan, BulkPlanStatus, User, create_notification
from config import db
from optimization import select_stocks_for_moyenne, select_stocks_with_minimum_quantities
from copper import copper_bp


@copper_bp.route('/optimize_stocks', methods=['GET', 'POST'])
def optimize_stocks():
    """Optimize copper stocks - THREE STEP process"""
    from copper.forms import CopperOptimizationForm
    
    # Initialize variables
    form = CopperOptimizationForm()
    selected_stocks = []
    achieved_moyenne = 0
    achieved_moyenne_nb = 0
    achieved_total_quantity = 0
    quantities = {}
    mode = None  # initial, edit, or result

    # If user arrived via a plain GET (no explicit mode param), clear any
    # previously-stored optimization state so the Optimize link always
    # opens the initial target-entry screen. Treat string 'None' or empty
    # values as absent.
    mode_arg = request.args.get('mode')
    if request.method == 'GET' and (mode_arg is None or (isinstance(mode_arg, str) and mode_arg.strip().lower() in ('', 'none'))):
        for _k in (
            'optimization_mode',
            'optimization_quantities',
            'optimization_target_moyenne',
            'optimization_target_moyenne_nb',
            'optimization_target_total_quantity',
            'optimization_edits',
            'optimization_changes',
        ):
            session.pop(_k, None)

    if form.validate_on_submit():
        target_moyenne = form.target_moyenne.data
        target_moyenne_nb = form.target_moyenne_nb.data
        target_total_quantity = form.target_total_quantity.data
        action = request.form.get('action', '')
        
        # ═══════════════════════════════════════════════════
        # STEP 1: User clicks "Filter Stocks" with targets
        # ═══════════════════════════════════════════════════
        if action == 'filter':
            # Auto-filter stocks based on target quality
            selected_stocks, achieved_moyenne, achieved_moyenne_nb, achieved_total_quantity = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb,
                target_total_quantity=target_total_quantity,
            )
            
            # Create quantity dict for display
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'initial'
        
        # ═══════════════════════════════════════════════════
        # STEP 2: User clicks "Edit Selection" to adjust
        # ═══════════════════════════════════════════════════
        elif action == 'edit':
            # Show ALL stocks for user to edit quantities
            selected_stocks, achieved_moyenne, achieved_moyenne_nb, achieved_total_quantity = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb,
                target_total_quantity=target_total_quantity,
                minimize_quantity=True,
            )
            
            # Initialize quantities from selected stocks
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'edit'
        
        # ═══════════════════════════════════════════════════
        # STEP 3: User clicks "Recalculate" with adjustments
        # ═══════════════════════════════════════════════════
        elif action == 'recalculate':
            # Capture user's adjusted quantities from form and session-saved edits
            minimum_quantities = {}

            # Load any page-saved edits from session (id -> qty)
            session_edits = session.get('optimization_edits', {}) or {}
            # Normalize to int->float map
            merged_edits = {}
            for k, v in session_edits.items():
                try:
                    merged_edits[int(k)] = float(v)
                except Exception:
                    continue

            # Fetch all candidate stocks once
            all_stocks_list = CopperStock.query.filter(CopperStock.local_balance > 0).all()
            changes = {}

            # Seed minimum_quantities from previously computed recommended quantities
            # so Recalculate starts from the recommended baseline and then applies edits.
            recommended = session.get('optimization_quantities', {}) or {}
            seeded_minima = {}
            for k, v in recommended.items():
                try:
                    seeded_minima[int(k)] = float(v)
                except Exception:
                    continue
            # Initialize with seeded minima (may be empty)
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

                # If user provided an explicit edit, overlay it onto seeded minima
                if s.id in merged_edits:
                    min_qty = merged_edits[s.id]
                    # Clamp values to available range
                    if min_qty < 0:
                        min_qty = 0.0
                    if min_qty > s.local_balance:
                        min_qty = float(s.local_balance)

                    # Determine baseline for comparison: prefer seeded baseline if present
                    baseline = seeded_minima.get(s.id, s.local_balance)
                    if abs(min_qty - baseline) > 0.01:
                        # Apply override (including explicit zero to exclude)
                        minimum_quantities[s.id] = min_qty
                        changes[s.id] = {'before': baseline, 'after': min_qty}
                    else:
                        # No meaningful change from baseline; ensure seeded baseline remains
                        if s.id in seeded_minima:
                            minimum_quantities[s.id] = seeded_minima[s.id]

            # Persist merged edits and changes back to session for continuity
            # store as strings to keep JSON serialization safe
            session['optimization_edits'] = {str(k): float(v) for k, v in merged_edits.items()}
            session['optimization_changes'] = {str(k): v for k, v in changes.items()}
            
            # Re-optimize with user's minimum quantities as constraints
            selected_stocks, achieved_moyenne, achieved_moyenne_nb, quantities = select_stocks_with_minimum_quantities(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb,
                minimum_quantities=minimum_quantities,
                target_total_quantity=target_total_quantity,
            )
            mode = 'result'
        
        # ═══════════════════════════════════════════════════
        # Back button: Return to initial form
        # ═══════════════════════════════════════════════════
        elif action == 'back_to_initial':
            mode = 'initial'
            selected_stocks, achieved_moyenne, achieved_moyenne_nb, achieved_total_quantity = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb,
                target_total_quantity=target_total_quantity,
            )
            quantities = {s.id: s.local_balance for s in selected_stocks}

    # Get all stocks to display in template (for edit mode)
    # If user performed a GET search while in edit mode (mode=edit),
    # re-run the filter to populate recommended quantities so the
    # search keeps the user in the 'edit' view instead of dropping
    # back to the initial page.
    if request.method == 'GET' and request.args.get('mode') == 'edit':
        try:
            # Prefer using session-saved recommended quantities (from previous Edit action)
            sess_qty = session.get('optimization_quantities') or {}
            if sess_qty:
                # Rehydrate ORM objects for selected stocks from session keys
                try:
                    ids = [int(k) for k in sess_qty.keys()]
                    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(ids)).all()
                    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
                    mode = 'edit'
                except Exception:
                    selected_stocks = []
                    quantities = {}
                    mode = None
            else:
                # Fall back to recomputing selection using provided or stored targets
                tgt_m = request.args.get('target_moyenne') or session.get('optimization_target_moyenne')
                tgt_nb = request.args.get('target_moyenne_nb') or session.get('optimization_target_moyenne_nb')
                tgt_total = request.args.get('target_total_quantity') or session.get('optimization_target_total_quantity')
                try:
                    tgt_m_f = float(tgt_m) if tgt_m not in (None, '') else None
                except Exception:
                    tgt_m_f = None
                selected_stocks, achieved_moyenne, achieved_moyenne_nb, achieved_total_quantity = select_stocks_for_moyenne(
                    target_moyenne=tgt_m_f,
                    target_moyenne_nb=(float(tgt_nb) if tgt_nb else None),
                    target_total_quantity=(float(tgt_total) if tgt_total else None),
                    minimize_quantity=True,
                )
                quantities = {s.id: s.local_balance for s in selected_stocks}
                mode = 'edit'
        except Exception:
            # Fall back to defaults if anything fails
            selected_stocks = []
            quantities = {}
            mode = None

    # Support server-side pagination and supplier search for the edit table
    # If the user returns via a plain GET (no mode param) but the last
    # optimization flow left them in 'edit', restore that state from session
    # so the page doesn't drop back to the welcome message.
    if request.method == 'GET' and mode is None:
        sess_mode = session.get('optimization_mode')
        try:
            # If the user was previously editing, restore edit mode so
            # searches and pagination continue to show the editable table.
            if sess_mode == 'edit':
                sess_qty = session.get('optimization_quantities') or {}
                if sess_qty:
                    ids = [int(k) for k in sess_qty.keys()]
                    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(ids)).all()
                    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
                    mode = 'edit'
            # If the last session mode was 'initial' (user previously filtered)
            # or there was no session mode, show the initial target-entry form
            # when clicking the Optimize Stocks link.
            elif sess_mode == 'initial' or sess_mode is None:
                mode = 'initial'
            # If session mode is 'result', prefer to show results when appropriate
            elif sess_mode == 'result':
                mode = 'result'
        except Exception:
            # ignore and continue with defaults
            pass

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

    base_query = CopperStock.query.filter(CopperStock.local_balance > 0)
    if q:
        # simple supplier search (case-insensitive)
        base_query = base_query.filter(CopperStock.supplier.ilike(f"%{q}%"))

    total_count = base_query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    all_stocks = base_query.order_by(CopperStock.date.desc()).offset((page - 1) * per_page).limit(per_page).all()

    # Store quantities in session for retrieve later (when form submits)
    # Only overwrite stored quantities when we have a non-empty set. This
    # prevents a plain GET (such as a supplier search) from wiping the
    # previously computed recommended quantities.
    if quantities:
        session['optimization_quantities'] = quantities

    # Only set optimization_mode when we have an explicit mode to avoid
    # accidentally clearing a previously active edit session on plain GETs.
    if mode is not None:
        session['optimization_mode'] = mode

    # Update stored target values only when provided by the current
    # request (POST form submission or explicit GET query params). Do
    # not overwrite existing session targets with empty/None values from
    # an unrelated GET (e.g., a simple search).
    def _set_session_target_if_present(key, value):
        if value is None:
            return
        if isinstance(value, str) and value.strip().lower() in ('', 'none'):
            return
        try:
            # store numeric targets as floats when possible
            session[key] = float(value)
        except Exception:
            session[key] = value

    # Prefer POSTed form values when available (i.e. after Filter/Edit actions)
    if request.method == 'POST' and form is not None:
        _set_session_target_if_present('optimization_target_moyenne', form.target_moyenne.data)
        _set_session_target_if_present('optimization_target_moyenne_nb', form.target_moyenne_nb.data)
        _set_session_target_if_present('optimization_target_total_quantity', form.target_total_quantity.data)
    else:
        # Otherwise, pick up explicit query params if present (e.g., search/pagination links)
        _set_session_target_if_present('optimization_target_moyenne', request.args.get('target_moyenne'))
        _set_session_target_if_present('optimization_target_moyenne_nb', request.args.get('target_moyenne_nb'))
        _set_session_target_if_present('optimization_target_total_quantity', request.args.get('target_total_quantity'))

    return render_template(
        'copper/optimize.html',
        selected_stocks=selected_stocks,
        all_stocks=all_stocks,
        achieved_moyenne=achieved_moyenne,
        achieved_moyenne_nb=achieved_moyenne_nb,
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


@copper_bp.route('/optimize_stocks/save_edits', methods=['POST'])
def save_optimize_edits():
    """AJAX endpoint to persist partial edits (id -> qty) into session."""
    try:
        payload = request.get_json() or {}
        edits = session.get('optimization_edits', {}) or {}
        for sid, qty in (payload.get('edits') or {}).items():
            try:
                sid_i = int(sid)
                qty_f = float(qty)
                # clamp non-negative
                if qty_f < 0:
                    continue
                edits[str(sid_i)] = qty_f
            except Exception:
                continue
        session['optimization_edits'] = edits
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False}), 400


@copper_bp.route('/optimize_stocks/totals', methods=['GET'])
def optimize_stocks_totals():
    """Return JSON with server-authoritative totals for the current optimization session."""
    try:
        quantities = session.get('optimization_quantities', {}) or {}
        # If session quantities empty, try to recompute using stored targets
        if not quantities:
            mode = session.get('optimization_mode')
            tgt = session.get('optimization_target_moyenne')
            tgt_nb = session.get('optimization_target_moyenne_nb')
            tgt_total = session.get('optimization_target_total_quantity')
            def _has_valid_target(val):
                if val is None:
                    return False
                if isinstance(val, str) and val.strip().lower() in ('', 'none'):
                    return False
                return True

            # If any target is present (not None/'None'/empty), recompute recommended quantities
            if _has_valid_target(tgt) or _has_valid_target(tgt_nb) or _has_valid_target(tgt_total):
                try:
                    # select_stocks_for_moyenne returns (selected_stocks, achieved_moyenne, achieved_moyenne_nb)
                    selected_stocks, achieved_moyenne, achieved_moyenne_nb = select_stocks_for_moyenne(
                        target_moyenne=tgt,
                        target_moyenne_nb=tgt_nb,
                        target_total_quantity=tgt_total,
                    )
                    quantities = {s.id: s.local_balance for s in selected_stocks}
                    # store back to session for future requests
                    session['optimization_quantities'] = quantities
                except Exception:
                    quantities = {}

        # ensure numeric values
        total = 0.0
        for v in quantities.values():
            try:
                total += float(v)
            except Exception:
                continue
        return jsonify({
            'total_recommended': total,
            'quantities': quantities,
        })
    except Exception:
        # Don't raise—return empty safe response
        return jsonify({'total_recommended': 0.0, 'quantities': {}})


@copper_bp.route('/optimize_stocks/confirm_output', methods=['POST'])
def confirm_bulk_output():
    """Record bulk output from optimization results"""
    from copper.forms import CopperOutputForm
    
    # Get form data
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    customer = request.form.get("customer")
    output_amount = float(request.form.get('output_amount') or 0)
    amount_paid = float(request.form.get('amount_paid') or 0)
    note = request.form.get("note") or "Bulk output from optimization"
    
    # Get quantities from session (stored when results page was rendered)
    quantities = session.get('optimization_quantities', {})
    
    # DEBUG: Log what we're receiving
    print(f"\n{'='*80}")
    tgt = session.get('optimization_target_moyenne')
    tgt_nb = session.get('optimization_target_moyenne_nb')
    tgt_total = session.get('optimization_target_total_quantity')
            # If any target is present, attempt to recompute selection server-side
    if tgt is not None or tgt_nb is not None or tgt_total is not None:
        print(f"   repr(): {repr(quantities)}")
        print(f"   Content: {quantities}")
        print(f"{'='*80}\n")
        target_moyenne=tgt,
        target_moyenne_nb=tgt_nb,
        target_total_quantity=tgt_total,
        return redirect(url_for('copper.optimize_stocks'))
    
    # DEBUG: Show what we're processing
    print(f"\n{'='*80}")
    print(f"🔍 BULK OUTPUT PROCESSING:")
    print(f"   Total stocks to process: {len(quantities)}")
    print(f"   Quantities: {quantities}")
    print(f"{'='*80}\n")
    
    # Calculate TOTAL quantity for proportional distribution
    total_qty = sum(float(qty) for qty in quantities.values())
    print(f"📊 Total Quantity: {total_qty} kg\n")
    
    # Generate readable batch_id: customer_name_date_hexcode
    hex_code = uuid.uuid4().hex[:6]
    date_str = date.strftime('%Y%m%d')
    customer_safe = customer.lower().replace(' ', '_')[:20]  # Make safe for ID
    batch_id = f"{customer_safe}_{date_str}_{hex_code}"
    
    print(f"📦 Batch ID: {batch_id}\n")
    
    # ------------------------------------------------------------------
    # 1) Build and store a BulkOutputPlan so the store keeper (and boss)
    #    can see the exact optimal table that was used here.
    # ------------------------------------------------------------------
    # Batch-fetch all involved stocks to avoid N+1 queries
    plan_items = []
    try:
        requested_ids = [int(sid) for sid in quantities.keys()]
    except Exception:
        requested_ids = []
    stocks_map = {}
    if requested_ids:
        stocks = CopperStock.query.filter(CopperStock.id.in_(requested_ids)).all()
        stocks_map = {s.id: s for s in stocks}

    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty_float = float(qty)
        except (ValueError, TypeError):
            continue

        stock = stocks_map.get(stock_id)
        if not stock or qty_float <= 0:
            continue

        plan_items.append({
            "stock_id": stock.id,
            "voucher_no": stock.voucher_no,
            "supplier": stock.supplier,
            "planned_output_kg": float(qty_float),
        })

    from flask_login import current_user

    plan = BulkOutputPlan(
        mineral_type="coltan",
        created_by_id=getattr(current_user, "id", None),
        status=BulkPlanStatus.SENT_TO_STORE.value,
        customer=customer,
        batch_id=batch_id,
        note=note,
        plan_json=plan_items,
    )
    db.session.add(plan)
    db.session.flush()  # so plan.id is available for notifications

    # Notify all active store keepers that a new copper bulk plan exists.
    store_keepers = User.query.filter_by(role="store_keeper", is_active=True).all()
    emails = []
    for sk in store_keepers:
        create_notification(
            user_id=sk.id,
            type_="BULK_PLAN_CREATED",
            message=(
                f"New coltan bulk output plan {plan.id} for customer {customer} "
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
        logging.exception("Failed to enqueue copper bulk plan email via Brevo")

    # ------------------------------------------------------------------
    # 2) Execute the actual outputs as before (business logic unchanged)
    # ------------------------------------------------------------------
    output_count = 0
    # Reuse batch-fetched stocks_map to process outputs (single try per stock)
    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty = float(qty)

            stock = stocks_map.get(stock_id) or CopperStock.query.get(stock_id)
            if not stock:
                continue

            # Validation: cannot output more than available
            if qty > stock.local_balance:
                flash(
                    f"⚠️ Stock {stock.voucher_no}: Cannot output {qty}kg, only {stock.local_balance}kg available",
                    "warning",
                )
                continue

            # Calculate PROPORTIONAL amounts for this stock
            proportion = qty / total_qty if total_qty > 0 else 0
            proportional_amount = output_amount * proportion
            proportional_paid = amount_paid * proportion

            # Create output record with proportional amounts
            out = CopperOutput(
                batch_id=batch_id,  # Group all stocks in this order together
                stock_id=stock.id,
                date=date,
                output_kg=qty,
                output_amount=proportional_amount,  # Proportional
                amount_paid=proportional_paid,  # Proportional
                customer=customer,
                note=note,
            )

            out.update_debt()
            db.session.add(out)
            db.session.flush()

            # Update stock's local balance
            stock.local_balance = stock.remaining_stock()

            # Recalculate t_unity for this stock
            stock.t_unity = (stock.nb or 0) * (stock.local_balance or 0)

            # IMPORTANT: Recalculate unit_percent based on new local_balance
            from utils import calculate_unit_percentage

            stock.unit_percent = calculate_unit_percentage(
                stock.local_balance,
                stock.percentage,
            )

            print("   After update:")
            print(f"   local_balance: {stock.local_balance}")
            print(f"   t_unity: {stock.t_unity}")
            print(f"   unit_percent: {stock.unit_percent}\n")

            output_count += 1
        except (ValueError, TypeError) as e:
            print(f"❌ Error processing stock {stock_id_str}: {e}\n")
            flash(f"❌ Error processing stock: {e}", "danger")
            continue

    # Mark the bulk plan as executed and record who executed it.
    plan.status = BulkPlanStatus.EXECUTED.value
    plan.executed_at = datetime.utcnow()
    plan.executed_by_id = getattr(current_user, "id", None)
    
    # Update global moyennes once after all changes
    CopperStock.update_global_moyennes()
    
    db.session.commit()
    
    # IMPORTANT: Clear session data after successful output to prevent duplicates
    # This ensures the same quantities won't be reused if user comes back to page
    session.pop('optimization_quantities', None)
    session.pop('optimization_mode', None)
    
    flash(f"✅ Bulk output recorded successfully! {output_count} stocks updated.", "success")
    return redirect(url_for('copper.optimize_stocks'))
