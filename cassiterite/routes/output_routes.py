"""
Cassiterite Output Routes - THREE-STEP Optimization Process
STEP 1 (mode="initial"): User enters target moyenne → Filter stocks (BINARY)
STEP 2 (mode="edit"): User clicks "Edit Selection" → Adjust quantities manually
STEP 3 (mode="result"): User clicks "Recalculate" → Hybrid optimization
"""
from flask import render_template, request, redirect, url_for, flash, session
from utils import safe_jsonify
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
import logging
logger = logging.getLogger(__name__)


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


@cassiterite_bp.route('/record_output', methods=['GET', 'POST'])
@role_required("accountant")
def record_output():
    """Record cassiterite output (single)"""
    form = RecordCassiteriteOutputForm()
    
    # Populate stock choices
    # Populate choices by selecting only required columns to avoid loading full ORM objects
    stock_rows = (
        db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier, CassiteriteStock.local_balance)
        .filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False))
        .order_by(CassiteriteStock.date.desc())
        .all()
    )
    form.stock_id.choices = [(r.id, f"{r.voucher_no} - {r.supplier} - ({r.local_balance}kg)") for r in stock_rows]
    
    if form.validate_on_submit():
        stock = CassiteriteStock.query.get(form.stock_id.data)
        
        if not stock or getattr(stock, "is_deleted", False):
            flash("Stock not found!", "error")
            return redirect(url_for('cassiterite.record_output'))

        currency = (form.currency.data or 'RWF').upper()
        exchange_rate_input = form.exchange_rate.data
        payment_stage = (form.payment_stage.data or 'full_settlement').strip().lower()
        
        # Handle optional fields: customer, output_amount, amount_paid
        customer = (form.customer.data or '').strip() or None
        output_amount = form.output_amount.data or 0
        amount_paid = form.amount_paid.data or 0
        
        try:
            output_amount_rwf, exchange_rate = _normalize_amount_to_rwf(output_amount, currency, exchange_rate_input)
            amount_paid_rwf, _ = _normalize_amount_to_rwf(amount_paid, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for('cassiterite.record_output'))
        
        # Create output
        output = CassiteriteOutput(
            stock_id=stock.id,
            date=form.date.data,
            output_kg=form.output_kg.data,
            customer=customer,
            output_amount=output_amount,
            output_amount_rwf=output_amount_rwf,
            amount_paid=amount_paid,
            amount_paid_rwf=amount_paid_rwf,
            currency=currency,
            exchange_rate=exchange_rate,
            payment_stage=payment_stage,
            note=form.note.data,
            voucher_no=stock.voucher_no
        )

        # Ensure debt_remaining is correctly calculated
        output.update_debt()

        # Compute old contribution before mutation
        try:
            old_q, old_wp, old_t = CassiteriteStock.contribution(stock)
        except Exception:
            old_q = old_wp = old_t = 0.0

        db.session.add(output)
        db.session.flush()

        # Recalculate stock and apply aggregate delta
        stock.update_calculations()
        try:
            new_q, new_wp, new_t = CassiteriteStock.contribution(stock)
            CassiteriteStock.apply_aggregate_delta(new_q - old_q, new_wp - old_wp, new_t - old_t)
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
        output_details = f"Stock: {stock.voucher_no}, Supplier: {stock.supplier}, Output: {form.output_kg.data} kg, Note: {form.note.data}"
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

        flash(f"Output of {form.output_kg.data}kg recorded. Customer and amount will be added later by the negotiator.", "success")
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
            all_stocks = db.session.query(
                CassiteriteStock.id,
                CassiteriteStock.voucher_no,
                CassiteriteStock.supplier,
                CassiteriteStock.local_balance,
            ).filter(
                CassiteriteStock.local_balance > 0,
                CassiteriteStock.is_deleted.is_(False),
            ).order_by(CassiteriteStock.date.desc()).all()
        
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
            all_stocks_list = db.session.query(
                CassiteriteStock.id,
                CassiteriteStock.voucher_no,
                CassiteriteStock.supplier,
                CassiteriteStock.local_balance,
            ).filter(
                CassiteriteStock.local_balance > 0,
                CassiteriteStock.is_deleted.is_(False),
            ).order_by(CassiteriteStock.date.desc()).all()

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
                    selected_stocks = CassiteriteStock.query.filter(
                        CassiteriteStock.id.in_(ids),
                        CassiteriteStock.is_deleted.is_(False),
                    ).all()
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
    base_q = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.voucher_no,
        CassiteriteStock.supplier,
        CassiteriteStock.local_balance,
    ).filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False),
    )
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
        # IMPORTANT: Store achieved moyenne for O(1) lookup in batch selector
        # This avoids recalculation when negotiator views the batch
        session['optimization_achieved_moyenne'] = float(achieved_moyenne) if achieved_moyenne else 0.0

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
        return safe_jsonify({'total_recommended': total, 'quantities': quantities})
    except Exception:
        return safe_jsonify({'total_recommended': 0.0, 'quantities': {}})


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
        return safe_jsonify({'ok': True})
    except Exception:
        return safe_jsonify({'ok': False}), 400


@cassiterite_bp.route('/confirm_bulk_output', methods=['POST'])
@role_required("accountant")
def confirm_bulk_output():
    """Create a cassiterite bulk output plan for store confirmation/execution.

    Accountant provides only the `date` and optional note; customer and
    monetary details are recorded by the negotiator when receipts are applied.
    """
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    note = request.form.get("note") or "Bulk output from optimization"
    
    # Get quantities from session
    quantities = session.get('optimization_quantities', {})
    
    if not quantities:
        flash("No quantities to output", "danger")
        return redirect(url_for('cassiterite.optimize'))
    
    try:
        total_qty = sum(float(qty) for qty in quantities.values())
        if total_qty == 0:
            flash("Total quantity is zero!", "error")
            return redirect(url_for('cassiterite.optimize'))

        hex_code = uuid4().hex[:6]
        date_str = date.strftime('%Y%m%d')
        batch_id = f"batch_{date_str}_{hex_code}"

        stock_ids = [int(k) for k in quantities.keys() if str(k).isdigit()]
        all_stocks = {
            s.id: s
            for s in CassiteriteStock.query.filter(
                CassiteriteStock.id.in_(stock_ids),
                CassiteriteStock.is_deleted.is_(False),
            ).all()
        } if stock_ids else {}

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

            proportion = qty_float / total_qty if total_qty > 0 else 0
            plan_items.append({
                "stock_id": stock.id,
                "voucher_no": stock.voucher_no,
                "supplier": stock.supplier,
                "planned_output_kg": float(qty_float),
                "quoted_amount_input": 0.0,
                "quoted_amount_rwf": 0.0,
                "currency": "RWF",
                "exchange_rate": 1.0,
            })

        if not plan_items:
            flash("No valid rows generated for this plan.", "error")
            return redirect(url_for('cassiterite.optimize'))

        from datetime import datetime

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
            stock = all_stocks.get(sid)
            if not stock:
                continue
            lb = float(getattr(stock, 'local_balance', 0) or 0)
            if lb <= 0:
                continue
            unit_per_kg = float(getattr(stock, 'unit_percent', 0) or 0) / lb
            tunity_per_kg = float(getattr(stock, 't_unity', 0) or 0) / lb
            total_unit += unit_per_kg * qty_f
            total_tunity += tunity_per_kg * qty_f

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
            mineral_type="cassiterite",
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
                    f"New cassiterite bulk output plan {plan.id} (batch {batch_id})"
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
            logging.exception("Failed to enqueue cassiterite bulk plan email via Brevo")

        session.pop('optimization_quantities', None)
        session.pop('optimization_mode', None)
        session.pop('optimization_target_moyenne', None)

        # Monetary/receipt details are recorded later by the negotiator
        flash(f"✓ Batch {batch_id} sent to store keeper for confirmation.", "success")
        return redirect(url_for('cassiterite.optimize'))
    
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
    batch_filter = (request.args.get('batch_id') or '').strip()
    date_from = request.args.get('from') or ''
    date_to = request.args.get('to') or ''

    q = CassiteriteOutput.query
    if customer_filter:
        q = q.filter(CassiteriteOutput.customer == customer_filter)
    if batch_filter:
        q = q.filter(CassiteriteOutput.batch_id == batch_filter)
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

    outputs = q.order_by(CassiteriteOutput.date.desc()).limit(200).all()

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
        'cassiterite/outputs.html',
        outputs=outputs,
        customer_filter=customer_filter,
        batch_filter=batch_filter,
        batch_summaries=batch_summaries,
        single_outputs=single_outputs,
        date_from=date_from,
        date_to=date_to,
    )


@cassiterite_bp.route('/optimize/direct_output', methods=['POST'])
@role_required("accountant")
def direct_bulk_output():
    """Directly create cassiterite outputs without going through negotiator workflow.
    
    Used for business error corrections where stock was already physically output
    but not yet recorded in the system. Bypasses approval and creates output records
    immediately with audit trail.
    """
    date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date() if request.form.get("date") else datetime.utcnow().date()
    reason = (request.form.get("reason") or "").strip()
    note = (request.form.get("note") or "").strip()
    
    if not reason:
        flash("Please select a reason for direct output.", "danger")
        return redirect(url_for('cassiterite.optimize'))
    
    if not note:
        flash("Please provide an explanation for the direct output.", "danger")
        return redirect(url_for('cassiterite.optimize'))

    quantities = session.get('optimization_quantities', {})
    if not quantities:
        flash("No selected quantities found. Please optimize again.", "danger")
        return redirect(url_for('cassiterite.optimize'))

    total_qty = sum(float(qty) for qty in quantities.values())
    if total_qty <= 0:
        flash("Total selected quantity must be greater than zero.", "danger")
        return redirect(url_for('cassiterite.optimize'))

    try:
        requested_ids = [int(sid) for sid in quantities.keys()]
    except Exception:
        requested_ids = []

    stocks_map = {}
    if requested_ids:
        stocks = CassiteriteStock.query.filter(
            CassiteriteStock.id.in_(requested_ids),
            CassiteriteStock.is_deleted.is_(False),
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
            output = CassiteriteOutput(
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
            logger.exception(f"Error creating direct output for stock {stock_id}: {e}")
            error_count += 1
            continue

    if output_count == 0:
        flash("No valid outputs were created. Please check your selections.", "danger")
        return redirect(url_for('cassiterite.optimize'))

    try:
        db.session.commit()
        
        # Create audit notification for managers
        managers = User.query.filter_by(role='boss', is_active=True).all()
        for manager in managers:
            create_notification(
                user_id=manager.id,
                type_="DIRECT_OUTPUT_RECORDED",
                message=f"Direct output recorded: {output_count} cassiterite stocks ({total_qty:.2f} kg) - Reason: {reason}",
                related_type="cassiterite_output",
                related_id=None,
            )
        
        success_msg = f"Successfully recorded {output_count} output(s) for {total_qty:.2f} kg directly."
        if error_count > 0:
            success_msg += f" ({error_count} failed)"
        flash(success_msg, "success")
        
        session.pop('optimization_quantities', None)
        session.pop('optimization_mode', None)
        
        return redirect(url_for('cassiterite.optimize'))
        
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Error committing direct outputs: {e}")
        flash(f"Error recording outputs: {str(e)}", "danger")
        return redirect(url_for('cassiterite.optimize'))
