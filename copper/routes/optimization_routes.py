"""
Optimization Routes
Handles copper stock optimization with three-step process:
STEP 1 (mode="initial"): User enters targets → System filters recommended stocks
STEP 2 (mode="edit"): User clicks "Edit Selection" → Shows ALL stocks for editing
STEP 3 (mode="result"): User adjusts quantities → System re-optimizes with constraints
"""
from flask import render_template, request, redirect, url_for, flash, session
from utils import safe_jsonify
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
    # Prefill form fields from session if available so searches/pagination
    # do not accidentally clear or display 'None' when the session holds
    # previously-entered targets.
    try:
        sess_tgt = session.get('optimization_target_moyenne')
        if (form.target_moyenne.data in (None, '')) and sess_tgt is not None:
            form.target_moyenne.data = sess_tgt
    except Exception:
        pass
    try:
        sess_tgt_nb = session.get('optimization_target_moyenne_nb')
        if (form.target_moyenne_nb.data in (None, '')) and sess_tgt_nb is not None:
            form.target_moyenne_nb.data = sess_tgt_nb
    except Exception:
        pass
    try:
        sess_total = session.get('optimization_target_total_quantity')
        if (form.target_total_quantity.data in (None, '')) and sess_total is not None:
            form.target_total_quantity.data = sess_total
    except Exception:
        pass
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
            all_stocks_list = CopperStock.query.filter(
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            ).all()
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
            # Compute achieved total quantity from returned quantities dict
            try:
                achieved_total_quantity = float(sum(float(v) for v in quantities.values())) if quantities else 0.0
            except Exception:
                achieved_total_quantity = 0.0
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
            sess_edits = session.get('optimization_edits', {}) or {}
            if sess_qty:
                # Rehydrate ORM objects for selected stocks from session keys
                try:
                    ids = [int(k) for k in sess_qty.keys()]
                    selected_stocks = CopperStock.query.filter(
                        CopperStock.id.in_(ids),
                        CopperStock.is_deleted.is_(False),
                    ).all()
                    # Start with recommended quantities
                    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
                    # Merge with user edits from session (edits override recommendations)
                    for k, v in sess_edits.items():
                        try:
                            sid = int(k)
                            qty = float(v)
                            quantities[sid] = qty
                        except Exception:
                            continue
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
                sess_edits = session.get('optimization_edits', {}) or {}
                if sess_qty:
                    ids = [int(k) for k in sess_qty.keys()]
                    selected_stocks = CopperStock.query.filter(
                        CopperStock.id.in_(ids),
                        CopperStock.is_deleted.is_(False),
                    ).all()
                    # Start with recommended quantities
                    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
                    # Merge with user edits from session (edits override recommendations)
                    for k, v in sess_edits.items():
                        try:
                            sid = int(k)
                            qty = float(v)
                            quantities[sid] = qty
                        except Exception:
                            continue
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
        # Default interactive page size to 10 for faster UI responsiveness
        per_page = int(request.args.get('per_page', 10))
        if per_page < 5:
            per_page = 5
        # Enforce a strict upper bound of 10 for optimization forms
        if per_page > 10:
            per_page = 10
    except Exception:
        per_page = 10

    # Cap edit-mode page size to improve responsiveness for the editable form.
    try:
        edit_mode_active = (mode == 'edit') or (request.args.get('mode') == 'edit') or (session.get('optimization_mode') == 'edit')
        if edit_mode_active:
            per_page = min(per_page, 10)
    except Exception:
        pass

    q = (request.args.get('q') or '').strip()

    base_query = CopperStock.query.filter(CopperStock.local_balance > 0, CopperStock.is_deleted.is_(False))
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
        # IMPORTANT: Store achieved moyenne/nb for O(1) lookup in batch selector
        # This avoids recalculation when negotiator views the batch
        session['optimization_achieved_moyenne'] = float(achieved_moyenne) if achieved_moyenne else 0.0
        session['optimization_achieved_moyenne_nb'] = float(achieved_moyenne_nb) if achieved_moyenne_nb else 0.0

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
        return safe_jsonify({'ok': True})
    except Exception:
        return safe_jsonify({'ok': False}), 400


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
                    # select_stocks_for_moyenne may return (selected_stocks, achieved_moyenne,
                    # achieved_moyenne_nb) or the extended (selected_stocks, achieved_moyenne,
                    # achieved_moyenne_nb, achieved_total_quantity). Accept both shapes.
                    res = select_stocks_for_moyenne(
                        target_moyenne=tgt,
                        target_moyenne_nb=tgt_nb,
                        target_total_quantity=tgt_total,
                    )
                    if isinstance(res, (list, tuple)) and len(res) >= 3:
                        selected_stocks = res[0]
                        achieved_moyenne = res[1]
                        achieved_moyenne_nb = res[2]
                        if len(res) >= 4:
                            try:
                                achieved_total_quantity = float(res[3] or 0)
                            except Exception:
                                achieved_total_quantity = 0.0
                    else:
                        selected_stocks, achieved_moyenne, achieved_moyenne_nb = [], 0, 0
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
        return safe_jsonify({
            'total_recommended': total,
            'quantities': quantities,
        })
    except Exception:
        # Don't raise—return empty safe response
        return safe_jsonify({'total_recommended': 0.0, 'quantities': {}})


@copper_bp.route('/optimize_stocks/confirm_output', methods=['POST'])
def confirm_bulk_output():
    """Create a bulk output plan and send it to store for execution.

    Accountant should only provide the `date` (and optional note). Monetary
    details and customer assignment are recorded later by the negotiator when
    receipts are recorded.
    """
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    # Do not accept customer/output_amount/amount_paid here — negotiator will
    # set customer and handle receipts later.
    note = request.form.get("note") or "Bulk output from optimization"

    quantities = session.get('optimization_quantities', {})
    if not quantities:
        flash("No selected quantities found. Please optimize again.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    # No currency/exchange handling here

    total_qty = sum(float(qty) for qty in quantities.values())
    if total_qty <= 0:
        flash("Total selected quantity must be greater than zero.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    hex_code = uuid.uuid4().hex[:6]
    date_str = date.strftime('%Y%m%d')
    # Use date + random code for batch id (no customer in batch id)
    batch_id = f"batch_{date_str}_{hex_code}"

    try:
        requested_ids = [int(sid) for sid in quantities.keys()]
    except Exception:
        requested_ids = []

    stocks_map = {}
    if requested_ids:
        stocks = CopperStock.query.filter(
            CopperStock.id.in_(requested_ids),
            CopperStock.is_deleted.is_(False),
        ).all()
        stocks_map = {s.id: s for s in stocks}

    plan_items = []
    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty_float = float(qty)
        except (ValueError, TypeError):
            continue

        stock = stocks_map.get(stock_id)
        if not stock or qty_float <= 0:
            continue

        proportion = qty_float / total_qty if total_qty > 0 else 0
        # Monetary quotes intentionally left zero at this stage; negotiator
        # will fill customer & amounts when recording receipts.
        plan_items.append({
            "stock_id": stock.id,
            "voucher_no": stock.voucher_no,
            "supplier": stock.supplier,
            "date": stock.date.isoformat() if stock.date else None,
            "planned_output_kg": float(qty_float),
            "percentage": float(stock.percentage or 0),
            "nobelium": float(stock.nb or 0) if stock.nb is not None else None,
            "quoted_amount_input": 0.0,
            "quoted_amount_rwf": 0.0,
            "currency": "RWF",
            "exchange_rate": 1.0,
        })

    if not plan_items:
        flash("No valid plan rows were generated. Please re-run optimization.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    from flask_login import current_user

    # Compute achieved quality deterministically from the quantities being submitted.
    # This avoids relying on session state and prevents zeros in negotiator views.
    total_unit = 0.0
    total_tunity = 0.0
    for item in plan_items:
        try:
            sid = int(item.get('stock_id'))
            qty_f = float(item.get('planned_output_kg') or 0.0)
        except Exception:
            continue
        if qty_f <= 0:
            continue
        stock = stocks_map.get(sid)
        if not stock:
            continue
        # Use percentage and nb directly to avoid dependency on potentially stale
        # unit_percent / t_unity values.
        pct = float(getattr(stock, 'percentage', 0) or 0)
        nb_val = float(getattr(stock, 'nb', 0) or 0)
        total_unit += (pct / 100.0) * qty_f
        total_tunity += nb_val * qty_f

    achieved_moyenne_val = float(total_unit / total_qty) if total_qty else 0.0
    achieved_moyenne_nb_val = float(total_tunity / total_qty) if total_qty else 0.0
    
    # Store metadata at top level of plan_json for quick retrieval
    plan_metadata = {
        "achieved_moyenne": float(achieved_moyenne_val) if achieved_moyenne_val else 0.0,
        "achieved_moyenne_nb": float(achieved_moyenne_nb_val) if achieved_moyenne_nb_val else 0.0,
        "created_at_iso": datetime.utcnow().isoformat(),
    }
    
    # Merge metadata with plan items for backward compatibility
    plan_json_full = [plan_metadata] + plan_items if plan_items else [plan_metadata]

    plan = BulkOutputPlan(
        mineral_type="coltan",
        created_by_id=getattr(current_user, "id", None),
        status=BulkPlanStatus.SENT_TO_STORE.value,
        customer=None,
        batch_id=batch_id,
        note=note,
        plan_json=plan_json_full,
    )
    db.session.add(plan)
    db.session.flush()

    store_keepers = User.query.filter_by(role="store_keeper", is_active=True).all()
    emails = []
    for sk in store_keepers:
        create_notification(
            user_id=sk.id,
            type_="BULK_PLAN_CREATED",
            message=(
                f"New coltan bulk output plan {plan.id} (batch {batch_id})"
            ),
            related_type="bulk_plan",
            related_id=plan.id,
        )
        if getattr(sk, 'email', None):
            emails.append(sk.email)

    db.session.commit()

    try:
        from flask_login import current_user
        from utils import send_brevo_email_async
        subject = f"Bulk Output Plan {plan.id} - Action Required"
        html_content = (
            f"<p>Dear Storekeeper,</p>"
            f"<p>A new bulk output plan (ID: {plan.id}, batch: {batch_id}) was created by {getattr(current_user, 'name', 'Unknown')}.</p>"
            f"<p>Note: {note}</p>"
            f"<p>Log into the system to review and execute the plan.</p>"
            "<p>Regards,<br>Urumuli Smart System</p>"
        )
        recipient_list = emails if emails else ["storekeeper@example.com"]
        send_brevo_email_async(subject, html_content, recipient_list)
    except Exception:
        import logging
        logging.exception("Failed to enqueue copper bulk plan email via Brevo")

    session.pop('optimization_quantities', None)
    session.pop('optimization_mode', None)

    # Any monetary/receipt details are recorded later by the negotiator
    flash("Bulk plan submitted. Store keeper must confirm stock before output is posted.", "success")
    return redirect(url_for('copper.optimize_stocks'))


@copper_bp.route('/optimize_stocks/direct_output', methods=['POST'])
def direct_bulk_output():
    """Directly create copper outputs without going through negotiator workflow.
    
    Used for business error corrections where stock was already physically output
    but not yet recorded in the system. Bypasses approval and creates output records
    immediately with audit trail.
    """
    from flask_login import current_user
    
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    reason = (request.form.get("reason") or "").strip()
    note = (request.form.get("note") or "").strip()
    
    if not reason:
        flash("Please select a reason for direct output.", "danger")
        return redirect(url_for('copper.optimize_stocks'))
    
    if not note:
        flash("Please provide an explanation for the direct output.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    quantities = session.get('optimization_quantities', {})
    if not quantities:
        flash("No selected quantities found. Please optimize again.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    total_qty = sum(float(qty) for qty in quantities.values())
    if total_qty <= 0:
        flash("Total selected quantity must be greater than zero.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    try:
        requested_ids = [int(sid) for sid in quantities.keys()]
    except Exception:
        requested_ids = []

    stocks_map = {}
    if requested_ids:
        stocks = CopperStock.query.filter(
            CopperStock.id.in_(requested_ids),
            CopperStock.is_deleted.is_(False),
        ).all()
        stocks_map = {s.id: s for s in stocks}

    output_count = 0
    error_count = 0
    
    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty_float = float(qty)
        except (ValueError, TypeError):
            error_count += 1
            continue

        stock = stocks_map.get(stock_id)
        if not stock or qty_float <= 0:
            error_count += 1
            continue

        try:
            # Create output record directly - no customer, no negotiator approval needed
            # All monetary fields left empty since this is just recording physical output
            output = CopperOutput(
                stock_id=stock_id,
                date=date,
                output_kg=qty_float,
                batch_id=None,  # No batch needed for direct outputs
                customer=None,  # No customer for error correction outputs
                output_amount=0.0,
                output_amount_rwf=0.0,
                amount_paid=0.0,
                amount_paid_rwf=0.0,
                currency='RWF',
                exchange_rate=1.0,
                payment_stage='PENDING_RECEIPT',  # Mark as pending customer/payment details
                debt_remaining=0.0,
                note=f"[DIRECT OUTPUT - {reason.upper()}] {note}\nRecorded by: {getattr(current_user, 'name', 'Unknown')} on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                voucher_no=stock.voucher_no,
            )
            db.session.add(output)
            output_count += 1
        except Exception as e:
            import logging
            logging.exception(f"Error creating direct output for stock {stock_id}: {e}")
            error_count += 1
            continue

    if output_count == 0:
        flash("No valid outputs were created. Please check your selections.", "danger")
        return redirect(url_for('copper.optimize_stocks'))

    try:
        db.session.commit()
        
        # Create audit notification for managers
        from core.models import create_notification
        managers = User.query.filter_by(role='boss', is_active=True).all()
        for manager in managers:
            create_notification(
                user_id=manager.id,
                type_="DIRECT_OUTPUT_RECORDED",
                message=f"Direct output recorded: {output_count} stocks ({total_qty:.2f} kg) - Reason: {reason}",
                related_type="copper_output",
                related_id=None,
            )
        
        success_msg = f"Successfully recorded {output_count} output(s) for {total_qty:.2f} kg directly."
        if error_count > 0:
            success_msg += f" ({error_count} failed)"
        flash(success_msg, "success")
        
        session.pop('optimization_quantities', None)
        session.pop('optimization_mode', None)
        
        return redirect(url_for('copper.optimize_stocks'))
        
    except Exception as e:
        db.session.rollback()
        import logging
        logging.exception(f"Error committing direct outputs: {e}")
        flash(f"Error recording outputs: {str(e)}", "danger")
        return redirect(url_for('copper.optimize_stocks'))
