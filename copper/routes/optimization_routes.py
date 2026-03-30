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
    quantities = {}
    mode = None  # initial, edit, or result

    if form.validate_on_submit():
        target_moyenne = form.target_moyenne.data
        target_moyenne_nb = form.target_moyenne_nb.data
        action = request.form.get('action', '')
        
        # ═══════════════════════════════════════════════════
        # STEP 1: User clicks "Filter Stocks" with targets
        # ═══════════════════════════════════════════════════
        if action == 'filter':
            # Auto-filter stocks based on target quality
            selected_stocks, achieved_moyenne, achieved_moyenne_nb = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb
            )
            
            # Create quantity dict for display
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'initial'
        
        # ═══════════════════════════════════════════════════
        # STEP 2: User clicks "Edit Selection" to adjust
        # ═══════════════════════════════════════════════════
        elif action == 'edit':
            # Show ALL stocks for user to edit quantities
            selected_stocks, achieved_moyenne, achieved_moyenne_nb = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb
            )
            
            # Initialize quantities from selected stocks
            quantities = {s.id: s.local_balance for s in selected_stocks}
            mode = 'edit'
        
        # ═══════════════════════════════════════════════════
        # STEP 3: User clicks "Recalculate" with adjustments
        # ═══════════════════════════════════════════════════
        elif action == 'recalculate':
            # Capture user's adjusted quantities from form
            minimum_quantities = {}
            
            # Get only remaining stocks to check form values (avoid loading inactive rows)
            all_stocks_list = CopperStock.query.filter(CopperStock.local_balance > 0).all()
            for s in all_stocks_list:
                qty_key = f'qty_{s.id}'
                if qty_key in request.form:
                    try:
                        user_qty = request.form[qty_key].strip()
                        if user_qty:  # Only if user entered something
                            min_qty = float(user_qty)
                            # Cap to available balance
                            min_qty = min(min_qty, s.local_balance)
                            
                            # ONLY add to minimum_quantities if user CHANGED from full balance
                            # If unchanged (equals full balance) → stays BINARY (PuLP decides 0 or all)
                            if abs(min_qty - s.local_balance) > 0.01:  # tolerance for float comparison
                                if min_qty > 0:
                                    minimum_quantities[s.id] = min_qty
                    except (ValueError, TypeError):
                        pass
            
            # Re-optimize with user's minimum quantities as constraints
            selected_stocks, achieved_moyenne, achieved_moyenne_nb, quantities = select_stocks_with_minimum_quantities(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb,
                minimum_quantities=minimum_quantities
            )
            mode = 'result'
        
        # ═══════════════════════════════════════════════════
        # Back button: Return to initial form
        # ═══════════════════════════════════════════════════
        elif action == 'back_to_initial':
            mode = 'initial'
            selected_stocks, achieved_moyenne, achieved_moyenne_nb = select_stocks_for_moyenne(
                target_moyenne=target_moyenne,
                target_moyenne_nb=target_moyenne_nb
            )
            quantities = {s.id: s.local_balance for s in selected_stocks}

    # Get all stocks to display in template (for edit mode)
    all_stocks = CopperStock.query.filter(CopperStock.local_balance > 0).all()

    # Store quantities in session for retrieve later (when form submits)
    # This avoids HTML/JavaScript quote escaping issues with JSON passing
    session['optimization_quantities'] = quantities
    session['optimization_mode'] = mode
    # store target values so totals endpoint can recompute if session quantities are missing
    try:
        session['optimization_target_moyenne'] = form.target_moyenne.data
        session['optimization_target_moyenne_nb'] = form.target_moyenne_nb.data
    except Exception:
        session['optimization_target_moyenne'] = None
        session['optimization_target_moyenne_nb'] = None

    return render_template(
        'copper/optimize.html',
        selected_stocks=selected_stocks,
        all_stocks=all_stocks,
        achieved_moyenne=achieved_moyenne,
        achieved_moyenne_nb=achieved_moyenne_nb,
        quantities=quantities,
        mode=mode,
        form=form
    )


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
            if tgt is not None:
                try:
                    # select_stocks_for_moyenne returns (selected_stocks, achieved_moyenne, achieved_moyenne_nb)
                    selected_stocks, achieved_moyenne, achieved_moyenne_nb = select_stocks_for_moyenne(
                        target_moyenne=tgt,
                        target_moyenne_nb=tgt_nb
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
    print(f"📌 RETRIEVED quantities FROM SESSION:")
    print(f"   Type: {type(quantities)}")
    print(f"   Length: {len(quantities)}")
    print(f"   repr(): {repr(quantities)}")
    print(f"   Content: {quantities}")
    print(f"{'='*80}\n")
    
    if not quantities:
        flash("❌ No quantities to output", "danger")
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
    plan_items = []
    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty_float = float(qty)
        except (ValueError, TypeError):
            continue

        stock = CopperStock.query.get(stock_id)
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
    for stock_id_str, qty in quantities.items():
        try:
            stock_id = int(stock_id_str)
            qty = float(qty)

            stock = CopperStock.query.get(stock_id)
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
