"""Core management routes (boss + store + notifications).

These routes are shared across the whole system and are not tied to a
single mineral module. They live under the `core` blueprint so that
`app.py` stays thin and focused on wiring, not logic.
"""

from flask import render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import current_user

from config import db
from core.auth import role_required
from core.models import (
    BulkOutputPlan,
    PaymentReview,
    PaymentReviewStatus,
    User,
    create_notification,
    Notification,
)
from . import core_bp
from sqlalchemy import func
from sqlalchemy.orm import joinedload


@core_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    """Simple profile page where users can change username, email and password."""
    if not getattr(current_user, 'is_authenticated', False):
        abort(403)

    user = User.query.get_or_404(current_user.id)

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip() or None
        password = (request.form.get('password') or '').strip()

        errors: list[str] = []
        if not username:
            errors.append('Username is required.')

        # Uniqueness checks (exclude self)
        if username and username != user.username:
            if User.query.filter(User.username == username, User.id != user.id).first():
                errors.append('Username is already taken.')
        if email and email != user.email:
            if User.query.filter(User.email == email, User.id != user.id).first():
                errors.append('Email is already in use.')

        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template('profile.html')

        # Save changes
        user.username = username or user.username
        user.email = email
        if password:
            user.set_password(password)
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('core.profile'))

    return render_template('profile.html')


# Central place to list the roles that make sense in this system.
# We add an explicit "admin" role so that one user can manage
# other users (create, edit, deactivate, delete).
ALLOWED_ROLES = ["admin", "boss", "accountant", "store_keeper"]


@core_bp.route("/boss/dashboard")
@role_required("boss","admin","accountant")
def boss_dashboard():
    """Boss-only consolidated company dashboard.

    Aggregates BOTH minerals (copper and cassiterite) and surfaces:
    - Gross profit per mineral and combined
    - Supplier and customer debts
    - Net position for the whole company
    - Pending payment approvals and recent bulk output plans
    """
    from copper.models import CopperStock, CopperOutput, WorkerPayment, SupplierPayment
    from cassiterite.models import CassiteriteStock, CassiteriteOutput, CassiteriteSupplierPayment

    # Copper metrics (use DB-side aggregates to avoid pulling full tables)
    # 1) Sales from customers
    copper_total_sales = db.session.query(func.coalesce(func.sum(CopperOutput.output_amount), 0)).scalar()

    # 2) Original cost basis from suppliers (book cost of all purchased stock)
    copper_cost_basis = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).scalar() or 0

    # 3) Inventory Value (current monetary value of remaining stock)
    #    Sum over remaining lots: net_balance * (local_balance / input_kg)
    #    This computes per-lot cost-per-kg * remaining kg so the KPI
    #    consistently reports monetary value (RWF) rather than physical kg.
    try:
        copper_inventory_value = db.session.query(
            func.coalesce(
                func.sum(CopperStock.net_balance * CopperStock.local_balance / func.nullif(CopperStock.input_kg, 0)),
                0,
            )
        ).filter(CopperStock.local_balance > 0, CopperStock.input_kg > 0).scalar() or 0
    except Exception:
        # fallback to kg-sum in the unlikely event the DB expression fails
        copper_inventory_value = db.session.query(
            func.coalesce(func.sum(CopperStock.local_balance), 0)
        ).filter(CopperStock.local_balance > 0).scalar() or 0

    # 4) Supplier payments already recorded against Coltan lots
    copper_supplier_payments = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).scalar() or 0

    # 5) Cost of stock sold (COGS) for copper: compute from recorded outputs
    # linked to lots so we attribute cost to goods actually sold.
    try:
        copper_cost_of_stock_sold = db.session.query(
            func.coalesce(
                func.sum(
                    CopperOutput.output_kg * (CopperStock.net_balance / func.nullif(CopperStock.input_kg, 0))
                ),
                0.0,
            )
        ).join(CopperStock, CopperOutput.stock_id == CopperStock.id).scalar() or 0
    except Exception:
        logger.exception("boss_dashboard: failed to compute copper COGS from outputs; falling back")
        copper_cost_of_stock_sold = (copper_cost_basis or 0) - (copper_inventory_value or 0)
    copper_gross_profit = (copper_total_sales or 0) - (copper_cost_of_stock_sold or 0)

    # 6) Supplier outstanding for Coltan = original supplier cost - payments.
    copper_supplier_debt = copper_cost_basis - copper_supplier_payments
    copper_customer_debt = db.session.query(func.coalesce(func.sum(CopperOutput.debt_remaining), 0)).scalar()
    # Subtract internal worker payments from cash position
    copper_worker_payments = db.session.query(func.coalesce(func.sum(WorkerPayment.amount), 0)).scalar()
    from cassiterite.models.workers_payment import CassiteriteWorkerPayment
    cass_worker_payments = db.session.query(func.coalesce(func.sum(CassiteriteWorkerPayment.amount), 0)).scalar()
    total_internal_worker_payments = copper_worker_payments + cass_worker_payments
    # Cash position for copper: sales - customer debts
    copper_cash_position = copper_total_sales - copper_customer_debt

    # Cassiterite metrics (use DB-side aggregates)
    # 1) Sales from customers
    cass_total_sales = db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_amount), 0)).scalar()

    # 2) Original cost basis from suppliers (book cost of all purchased cassiterite)
    cass_cost_basis = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).scalar() or 0

    # 3) Inventory Value (current monetary value of remaining cassiterite)
    try:
        cass_inventory_value = db.session.query(
            func.coalesce(
                func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / func.nullif(CassiteriteStock.input_kg, 0)),
                0,
            )
        ).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.input_kg > 0).scalar() or 0
    except Exception:
        cass_inventory_value = db.session.query(
            func.coalesce(func.sum(CassiteriteStock.local_balance), 0)
        ).filter(CassiteriteStock.local_balance > 0).scalar() or 0

    # 4) Supplier payments already recorded against Cassiterite lots
    cass_supplier_payments = db.session.query(func.coalesce(func.sum(CassiteriteSupplierPayment.amount), 0)).scalar() or 0

    # 5) COGS for cassiterite computed from outputs linked to lots
    try:
        cass_cogs = db.session.query(
            func.coalesce(
                func.sum(
                    CassiteriteOutput.output_kg * (CassiteriteStock.balance_to_pay / func.nullif(CassiteriteStock.input_kg, 0))
                ),
                0.0,
            )
        ).join(CassiteriteStock, CassiteriteOutput.stock_id == CassiteriteStock.id).scalar() or 0
    except Exception:
        logger.exception("boss_dashboard: failed to compute cassiterite COGS from outputs; falling back")
        cass_cogs = (cass_cost_basis or 0) - (cass_inventory_value or 0)
    cass_cost_of_stock_sold = cass_cogs
    cass_gross_profit = (cass_total_sales or 0) - (cass_cogs or 0)

    # 6) Supplier outstanding for Cassiterite = original supplier cost - payments
    cass_supplier_debt = cass_cost_basis - cass_supplier_payments
    cass_customer_debt = db.session.query(func.coalesce(func.sum(CassiteriteOutput.debt_remaining), 0)).scalar()
    # Cash position for cassiterite: sales - customer debts
    cass_cash_position = cass_total_sales - cass_customer_debt

    # Combined KPIs
    total_gross_profit = copper_gross_profit + cass_gross_profit
    # Combined Inventory Value (current cost of remaining stock for both minerals)
    total_inventory_value = (copper_inventory_value or 0) + (cass_inventory_value or 0)
    # Combined original cost basis and COGS
    # Note: we prefer reporting Inventory Value and COGS; initial/purchased
    # cost (total_initial_cost) is not used for gross profit reporting here.
    total_cost_of_stock_sold = (copper_cost_of_stock_sold or 0) + (cass_cost_of_stock_sold or 0)
    # Combined supplier debt (remaining obligations to all suppliers).
    total_supplier_debt = copper_supplier_debt + cass_supplier_debt
    total_customer_debt = copper_customer_debt + cass_customer_debt
    # Combined cash at hand: (total sales) - (customer debts) - (internal worker payments)
    total_sales = copper_total_sales + cass_total_sales
    total_cash_at_hand = total_sales - total_customer_debt - total_internal_worker_payments

    pending_reviews = PaymentReview.query.filter_by(
        status=PaymentReviewStatus.PENDING_REVIEW.value
    ).order_by(PaymentReview.created_at.desc()).all()

    # Recent approvals/rejections (exclude pending) - collapse multiple
    # review records for the same payment into a single entry so that
    # edits and their approvals appear as a single consolidated item.
    from datetime import datetime as _dt
    raw_reviews = (
        PaymentReview.query
        .filter(PaymentReview.status != PaymentReviewStatus.PENDING_REVIEW.value)
        .order_by(PaymentReview.created_at.desc())
        .limit(200)
        .all()
    )
    # Keep the most recent review per payment_id (fallback to review.id)
    by_payment: dict = {}
    for r in raw_reviews:
        key = r.payment_id if r.payment_id is not None else f"rev-{r.id}"
        best = by_payment.get(key)
        # Determine recency using reviewed_at if present, otherwise created_at
        r_time = r.reviewed_at or r.created_at
        best_time = (best.reviewed_at or best.created_at) if best else None
        if not best or (r_time and (not best_time or r_time > best_time)):
            by_payment[key] = r
    recent_reviews = sorted(
        by_payment.values(),
        key=lambda x: (x.reviewed_at or x.created_at) or _dt.min,
        reverse=True,
    )[:20]

    recent_plans = BulkOutputPlan.query.order_by(
        BulkOutputPlan.created_at.desc()
    ).limit(20).all()

    # Only boss/admin should see the payment approval table.
    show_payment_reviews = getattr(current_user, "role", None) in {"boss", "admin"}

    return render_template(
        "boss/dashboard.html",
        copper_total_sales=copper_total_sales,
        copper_cost_basis=copper_cost_basis,
        # Inventory Value (Coltan)
        copper_inventory_value=copper_inventory_value,
        copper_cost_of_stock_sold=copper_cost_of_stock_sold,
        copper_gross_profit=copper_gross_profit,
        copper_supplier_debt=copper_supplier_debt,
        copper_customer_debt=copper_customer_debt,
        copper_cash_position=copper_cash_position,
        cass_total_sales=cass_total_sales,
        cass_cost_basis=cass_cost_basis,
        # Inventory Value (Cassiterite)
        cass_inventory_value=cass_inventory_value,
        cass_cost_of_stock_sold=cass_cost_of_stock_sold,
        cass_gross_profit=cass_gross_profit,
        cass_supplier_debt=cass_supplier_debt,
        cass_customer_debt=cass_customer_debt,
        cass_cash_position=cass_cash_position,
        total_gross_profit=total_gross_profit,
        # Combined Inventory Value (Coltan + Cassiterite)
        total_inventory_value=total_inventory_value,
        total_cost_of_stock_sold=total_cost_of_stock_sold,
        total_supplier_debt=total_supplier_debt,
        total_customer_debt=total_customer_debt,
        total_internal_worker_payments=total_internal_worker_payments,
        total_cash_at_hand=total_cash_at_hand,
        pending_reviews=pending_reviews,
        show_payment_reviews=show_payment_reviews,
        recent_reviews=recent_reviews,
        recent_plans=recent_plans,
    )


@core_bp.route("/boss/dashboard/data")
@role_required("boss","admin","accountant")
def boss_dashboard_data():
    """Return dashboard data as JSON for AJAX updates.

    Supports optional query params: mineral, from, to
    """
    mineral = request.args.get('mineral') or ''
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None

    # Parse date filters into date objects (input type=date -> YYYY-MM-DD)
    from datetime import datetime, time
    date_from_str = date_from
    date_to_str = date_to
    date_from_obj = None
    date_to_obj = None
    try:
        if date_from_str:
            date_from_obj = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        if date_to_str:
            date_to_obj = datetime.strptime(date_to_str, "%Y-%m-%d").date()
    except Exception:
        # ignore parse errors and treat as no filter
        date_from_obj = None
        date_to_obj = None

    # reuse the KPI computations but apply mineral/date filters when provided
    from copper.models import CopperStock, CopperOutput, WorkerPayment, SupplierPayment
    from cassiterite.models import CassiteriteStock, CassiteriteOutput, CassiteriteSupplierPayment
    from cassiterite.models.workers_payment import CassiteriteWorkerPayment

    def compute_copper(d_from=None, d_to=None):
        # Filter outputs by output.date when date range provided (customer side)
        q_outputs = CopperOutput.query
        if d_from:
            q_outputs = q_outputs.filter(CopperOutput.date >= d_from)
        if d_to:
            q_outputs = q_outputs.filter(CopperOutput.date <= d_to)
        # Compute aggregates on the DB side to avoid loading full tables
        copper_total_sales = q_outputs.with_entities(func.coalesce(func.sum(CopperOutput.output_amount), 0)).scalar() or 0

        # Stock-side: restrict to lots in the date window (original supplier
        # cost basis is by stock.date).
        stock_q = CopperStock.query
        if d_from:
            stock_q = stock_q.filter(CopperStock.date >= d_from)
        if d_to:
            stock_q = stock_q.filter(CopperStock.date <= d_to)

        # Original cost basis for this window
        copper_cost_basis = stock_q.with_entities(func.coalesce(func.sum(CopperStock.net_balance), 0)).scalar() or 0

        # Inventory Value (current cost of remaining Coltan stock in this window)
        inv_q = stock_q.filter(CopperStock.local_balance > 0, CopperStock.input_kg > 0)
        copper_inventory_value = inv_q.with_entities(
            func.coalesce(
                func.sum(CopperStock.net_balance * CopperStock.local_balance / CopperStock.input_kg),
                0,
            )
        ).scalar() or 0

        # Supplier payments filtered by the same stock window
        pay_q = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).join(
            CopperStock, SupplierPayment.stock_id == CopperStock.id
        )
        if d_from:
            pay_q = pay_q.filter(CopperStock.date >= d_from)
        if d_to:
            pay_q = pay_q.filter(CopperStock.date <= d_to)
        copper_supplier_payments = pay_q.scalar() or 0

        # COGS for this window and gross profit = Sales - COGS
        copper_cogs = (copper_cost_basis or 0) - (copper_inventory_value or 0)
        copper_gross_profit = (copper_total_sales or 0) - (copper_cogs or 0)

        # Supplier debt = original supplier cost - payments
        copper_supplier_debt = copper_cost_basis - copper_supplier_payments
        copper_customer_debt = q_outputs.with_entities(func.coalesce(func.sum(CopperOutput.debt_remaining), 0)).scalar()
        # Worker payments may have a paid_at/datetime field; filter by date if available
        wp_q = WorkerPayment.query
        try:
            # prefer paid_at attribute if present
            if d_from:
                wp_q = wp_q.filter(WorkerPayment.paid_at >= datetime.combine(d_from, time.min))
            if d_to:
                wp_q = wp_q.filter(WorkerPayment.paid_at <= datetime.combine(d_to, time.max))
        except Exception:
            # model may not have paid_at or filtering may fail; fall back to all
            wp_q = WorkerPayment.query
        copper_worker_payments = wp_q.with_entities(func.coalesce(func.sum(WorkerPayment.amount), 0)).scalar()
        copper_cash_position = copper_total_sales - copper_customer_debt
        return {
            'total_sales': copper_total_sales,
            # Inventory Value (Coltan)
            'inventory_value': copper_inventory_value,
            'gross_profit': copper_gross_profit,
            'cogs': (copper_cost_basis or 0) - (copper_inventory_value or 0),
            'supplier_debt': copper_supplier_debt,
            'customer_debt': copper_customer_debt,
            'worker_payments': copper_worker_payments,
            'cash_position': copper_cash_position,
        }

    def compute_cass(d_from=None, d_to=None):
        # Customer side
        q_outputs = CassiteriteOutput.query
        if d_from:
            q_outputs = q_outputs.filter(CassiteriteOutput.date >= d_from)
        if d_to:
            q_outputs = q_outputs.filter(CassiteriteOutput.date <= d_to)
        cass_total_sales = q_outputs.with_entities(func.coalesce(func.sum(CassiteriteOutput.output_amount), 0)).scalar() or 0

        # Stock-side cost basis by lot.date
        stock_q = CassiteriteStock.query
        if d_from:
            stock_q = stock_q.filter(CassiteriteStock.date >= d_from)
        if d_to:
            stock_q = stock_q.filter(CassiteriteStock.date <= d_to)

        cass_cost_basis = stock_q.with_entities(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).scalar() or 0

        inv_q = stock_q.filter(CassiteriteStock.local_balance > 0, CassiteriteStock.input_kg > 0)
        cass_inventory_value = inv_q.with_entities(
            func.coalesce(
                func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / CassiteriteStock.input_kg),
                0,
            )
        ).scalar() or 0

        pay_q = db.session.query(func.coalesce(func.sum(CassiteriteSupplierPayment.amount), 0)).join(
            CassiteriteStock, CassiteriteSupplierPayment.stock_id == CassiteriteStock.id
        )
        if d_from:
            pay_q = pay_q.filter(CassiteriteStock.date >= d_from)
        if d_to:
            pay_q = pay_q.filter(CassiteriteStock.date <= d_to)
        cass_supplier_payments = pay_q.scalar() or 0

        # COGS for this window and gross profit = Sales - COGS
        cass_cogs = (cass_cost_basis or 0) - (cass_inventory_value or 0)
        cass_gross_profit = (cass_total_sales or 0) - (cass_cogs or 0)
        cass_supplier_debt = cass_cost_basis - cass_supplier_payments
        cass_customer_debt = q_outputs.with_entities(func.coalesce(func.sum(CassiteriteOutput.debt_remaining), 0)).scalar()
        wp_q = CassiteriteWorkerPayment.query
        try:
            if d_from:
                wp_q = wp_q.filter(CassiteriteWorkerPayment.paid_at >= datetime.combine(d_from, time.min))
            if d_to:
                wp_q = wp_q.filter(CassiteriteWorkerPayment.paid_at <= datetime.combine(d_to, time.max))
        except Exception:
            wp_q = CassiteriteWorkerPayment.query
        cass_worker_payments = wp_q.with_entities(func.coalesce(func.sum(CassiteriteWorkerPayment.amount), 0)).scalar()
        cass_cash_position = cass_total_sales - cass_customer_debt
        return {
            'total_sales': cass_total_sales,
            # Inventory Value (Cassiterite)
            'inventory_value': cass_inventory_value,
            'gross_profit': cass_gross_profit,
            'cogs': (cass_cost_basis or 0) - (cass_inventory_value or 0),
            'supplier_debt': cass_supplier_debt,
            'customer_debt': cass_customer_debt,
            'worker_payments': cass_worker_payments,
            'cash_position': cass_cash_position,
        }

    # Always compute per-mineral KPIs so the UI can show both
    # (the "mineral" filter only affects the recent plans table)
    copper = compute_copper(date_from_obj, date_to_obj)
    cass = compute_cass(date_from_obj, date_to_obj)

    # combine
    total_gross_profit = (copper['gross_profit'] if copper else 0) + (cass['gross_profit'] if cass else 0)
    # Inventory Value (combined) using per-mineral inventory_value fields
    total_inventory_value = (copper['inventory_value'] if copper else 0) + (cass['inventory_value'] if cass else 0)
    total_supplier_debt = (copper['supplier_debt'] if copper else 0) + (cass['supplier_debt'] if cass else 0)
    total_customer_debt = (copper['customer_debt'] if copper else 0) + (cass['customer_debt'] if cass else 0)
    total_internal_worker_payments = (copper['worker_payments'] if copper else 0) + (cass['worker_payments'] if cass else 0)
    total_sales = (copper['total_sales'] if copper else 0) + (cass['total_sales'] if cass else 0)
    total_cash_at_hand = total_sales - total_customer_debt - total_internal_worker_payments

    # recent plans (no pagination) - apply mineral and optional date filters
    plans_q = BulkOutputPlan.query.order_by(BulkOutputPlan.created_at.desc())
    if mineral:
        plans_q = plans_q.filter_by(mineral_type=mineral)
    # Use datetime range for created_at comparisons
    if date_from_obj:
        plans_q = plans_q.filter(BulkOutputPlan.created_at >= datetime.combine(date_from_obj, time.min))
    if date_to_obj:
        plans_q = plans_q.filter(BulkOutputPlan.created_at <= datetime.combine(date_to_obj, time.max))

    plans = plans_q.limit(50).all()
    recent_plans = []
    for p in plans:
        total_kg = 0
        if p.plan_json:
            for row in p.plan_json:
                # plan_json rows may be dict-like
                total_kg += (row.get('planned_output_kg') if isinstance(row, dict) else (row.planned_output_kg or 0))
        recent_plans.append({
            'id': p.id,
            'created_at': p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else None,
            'mineral_type': p.mineral_type,
            'customer': p.customer,
            'batch_id': p.batch_id,
            'status': p.status,
            'total_kg': total_kg,
        })

    kpis = {
        'total_gross_profit': total_gross_profit,
        # Combined Inventory Value for the current filters
        'total_inventory_value': total_inventory_value,
        # 'total_initial_cost' intentionally removed: UI no longer shows original purchased cost
        'total_cost_of_stock_sold': (copper.get('cogs') if copper else 0) + (cass.get('cogs') if cass else 0),
        'total_supplier_debt': total_supplier_debt,
        'total_customer_debt': total_customer_debt,
        'total_internal_worker_payments': total_internal_worker_payments,
        'total_cash_at_hand': total_cash_at_hand,
    }

    return jsonify({
        'kpis': kpis,
        'copper': copper,
        'cassiterite': cass,
        'recent_plans': recent_plans,
    })


@core_bp.route("/boss/payment_review/<int:review_id>/approve", methods=["POST"])
@role_required("boss")
def boss_approve_payment(review_id: int):
    """Boss approves a pending payment review.

    When this happens we also notify all active accountants so they can
    see that the boss has approved the payment.
    """
    review = PaymentReview.query.get_or_404(review_id)

    # 1) Update review status and metadata
    review.status = PaymentReviewStatus.APPROVED.value
    review.reviewed_by_id = getattr(current_user, "id", None)
    from datetime import datetime as _dt
    review.reviewed_at = _dt.utcnow()

    # 2) Build a short message describing what was approved
    message = (
        f"Boss approved {review.mineral_type} payment for "
        f"{review.customer} ({review.amount} {review.currency})."
    )

    # 3) Notify all active accountants.
    #    You could later change this to only notify the
    #    accountant who created the payment (review.created_by_id).
    accountant_rows = db.session.query(User.id).filter_by(role="accountant", is_active=True).all()
    for (acc_id,) in accountant_rows:
        create_notification(
            user_id=acc_id,
            type_="PAYMENT_REVIEW_APPROVED",
            message=message,
            related_type="payment_review",
            related_id=review.id,
        )

    # 4) Commit both the review update and notifications in one go
    db.session.commit()

    flash("Payment review approved.", "success")
    return redirect(url_for("core.boss_dashboard"))


@core_bp.route("/boss/payment_review/<int:review_id>/reject", methods=["POST"])
@role_required("boss")
def boss_reject_payment(review_id: int):
    """Boss rejects a pending payment review with an optional comment.

    Just like approvals, we send a notification to accountants so they
    understand that this payment was rejected and why.
    """
    review = PaymentReview.query.get_or_404(review_id)

    # 1) Update review status and store boss comment
    review.status = PaymentReviewStatus.REJECTED.value
    review.reviewed_by_id = getattr(current_user, "id", None)
    comment = request.form.get("boss_comment", "")
    review.boss_comment = comment
    from datetime import datetime as _dt
    review.reviewed_at = _dt.utcnow()

    # 2) Build a short message describing the rejection
    extra_reason = f" Reason: {comment}" if comment else ""
    message = (
        f"Boss rejected {review.mineral_type} payment for "
        f"{review.customer} ({review.amount} {review.currency})." + extra_reason
    )

    # 3) Notify all active accountants of the rejection
    accountant_rows = db.session.query(User.id).filter_by(role="accountant", is_active=True).all()
    for (acc_id,) in accountant_rows:
        create_notification(
            user_id=acc_id,
            type_="PAYMENT_REVIEW_REJECTED",
            message=message,
            related_type="payment_review",
            related_id=review.id,
        )

    # 4) Commit changes and notifications
    db.session.commit()

    flash("Payment review rejected.", "warning")
    return redirect(url_for("core.boss_dashboard"))


@core_bp.route("/store/dashboard")
@role_required("store_keeper")
def store_dashboard():
    """Store keeper dashboard focused on bulk output plans and notifications."""

    plans = BulkOutputPlan.query.order_by(
        BulkOutputPlan.created_at.desc()
    ).limit(50).all()

    user_notifications = []
    if getattr(current_user, "is_authenticated", False):
        # Show all unread notifications and up to 10 already-read notifications
        unread = (
            Notification.query.options(joinedload(Notification.user))
            .filter_by(user_id=current_user.id, read_at=None)
            .order_by(Notification.created_at.desc())
            .all()
        )
        read = (
            Notification.query.options(joinedload(Notification.user))
            .filter(Notification.user_id == current_user.id, Notification.read_at != None)
            .order_by(Notification.created_at.desc())
            .limit(10)
            .all()
        )
        user_notifications = unread + read

    return render_template(
        "store/dashboard.html",
        plans=plans,
        notifications=user_notifications,
        unread_notifications_count=Notification.query.filter_by(user_id=current_user.id, read_at=None).count(),
    )


@core_bp.route("/boss/copper/customer_ledger/<customer>")
@role_required("boss", "admin")
def boss_copper_customer_ledger(customer: str):
    """Boss/admin read-only view of a copper customer ledger.

    Reuses the same aggregation logic as copper.routes.debt_routes.customer_ledger
    but is exposed under the core blueprint so it is not blocked by
    accountant-only protections on the copper blueprint.
    """
    from collections import defaultdict
    from copper.models import CopperOutput

    outputs = (
        CopperOutput.query
        .filter_by(customer=customer)
        .order_by(CopperOutput.date)
        .all()
    )

    by_date = defaultdict(lambda: {"output_amount": 0, "amount_paid": 0, "output_kg": 0})
    for output in outputs:
        date_key = output.date
        by_date[date_key]["output_amount"] += (output.output_amount or 0)
        by_date[date_key]["amount_paid"] += (output.amount_paid or 0)
        by_date[date_key]["output_kg"] += (output.output_kg or 0)

    ledger = []
    running_balance = 0
    for date in sorted(by_date.keys()):
        data = by_date[date]
        running_balance += data["output_amount"]
        ledger.append({
            "date": date,
            "description": f"Total Output ({data['output_kg']} kg)",
            "debit": data["output_amount"],
            "credit": 0,
            "balance": running_balance,
            "output_kg": data["output_kg"],
            "batch_id": None,
        })

        if data["amount_paid"] > 0:
            running_balance -= data["amount_paid"]
            ledger.append({
                "date": date,
                "description": "Payment",
                "debit": 0,
                "credit": data["amount_paid"],
                "balance": running_balance,
                "output_kg": 0,
                "batch_id": None,
            })

    return render_template(
        "copper/customer_ledger.html",
        customer=customer,
        ledger=ledger,
        total_owed=db.session.query(func.coalesce(func.sum(CopperOutput.output_amount), 0)).filter(CopperOutput.customer == customer).scalar(),
        total_paid=db.session.query(func.coalesce(func.sum(CopperOutput.amount_paid), 0)).filter(CopperOutput.customer == customer).scalar(),
        remaining=(db.session.query(func.coalesce(func.sum(CopperOutput.output_amount), 0)).filter(CopperOutput.customer == customer).scalar() - db.session.query(func.coalesce(func.sum(CopperOutput.amount_paid), 0)).filter(CopperOutput.customer == customer).scalar()),
        user_role=getattr(current_user, 'role', None),
    )


@core_bp.route("/boss/cassiterite/customer_ledger/<customer>")
@role_required("boss", "admin")
def boss_cassiterite_customer_ledger(customer: str):
    """Boss/admin read-only view of a cassiterite customer ledger.

    Shows each individual output and payment row so the boss
    sees detailed movements instead of daily aggregates.
    """

    from cassiterite.models import CassiteriteOutput

    outputs = (
        CassiteriteOutput.query
        .filter_by(customer=customer)
        .order_by(CassiteriteOutput.date, CassiteriteOutput.id)
        .all()
    )

    ledger = []
    running_balance = 0.0

    for output in outputs:
        amount = float(output.output_amount or 0.0)
        paid = float(output.amount_paid or 0.0)

        # Output row (sale)
        if amount:
            running_balance += amount
            ledger.append({
                "date": output.date,
                "description": "Output",  # template adds kg details
                "debit": amount,
                "credit": 0.0,
                "balance": running_balance,
                "output_kg": float(output.output_kg or 0.0),
                "batch_id": output.batch_id,
                "voucher_no": output.voucher_no,
            })

        # Payment row (if any amount was paid for this output)
        if paid:
            running_balance -= paid
            ledger.append({
                "date": output.date,
                "description": "Payment",  # tied to this output
                "debit": 0.0,
                "credit": paid,
                "balance": running_balance,
                "output_kg": 0.0,
                "batch_id": output.batch_id,
                "voucher_no": output.voucher_no,
            })

    return render_template(
        "cassiterite/customer_ledger.html",
        customer=customer,
        ledger=ledger,
        total_owed=db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_amount), 0)).filter(CassiteriteOutput.customer == customer).scalar(),
        total_paid=db.session.query(func.coalesce(func.sum(CassiteriteOutput.amount_paid), 0)).filter(CassiteriteOutput.customer == customer).scalar(),
        remaining=(db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_amount), 0)).filter(CassiteriteOutput.customer == customer).scalar() - db.session.query(func.coalesce(func.sum(CassiteriteOutput.amount_paid), 0)).filter(CassiteriteOutput.customer == customer).scalar()),
        user_role=getattr(current_user, 'role', None),
    )


@core_bp.route("/boss/copper/supplier_ledger/<supplier>")
@role_required("boss", "admin")
def boss_copper_supplier_ledger(supplier: str):
    """Boss/admin read-only view of copper supplier ledger."""
    from copper.models import CopperStock, SupplierPayment

    # Eager-load payments to avoid N+1 queries when iterating stocks
    stocks = (
        CopperStock.query.options(joinedload(CopperStock.supplier_payments))
        .filter_by(supplier=supplier)
        .order_by(CopperStock.date)
        .all()
    )

    ledger = []
    running_balance = 0
    for stock in stocks:
        running_balance += (stock.net_balance or 0)
        ledger.append({
            "date": stock.date,
            "description": f"Stock {stock.voucher_no}",
            "debit": stock.net_balance,
            "credit": 0,
            "balance": running_balance,
        })

        # supplier_payments were eager-loaded above to prevent N+1
        for payment in stock.supplier_payments:
            running_balance -= (payment.amount or 0)
            ledger.append({
                "date": payment.paid_at,
                "description": f"Payment (Ref: {payment.reference})",
                "debit": 0,
                "credit": payment.amount,
                "balance": running_balance,
            })

    # Compute totals using DB aggregates to avoid re-summing the ledger list
    total_owed = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(CopperStock.supplier == supplier).scalar()
    total_paid = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).join(CopperStock, CopperStock.id == SupplierPayment.stock_id).filter(CopperStock.supplier == supplier).scalar()
    balance = (total_owed or 0) - (total_paid or 0)

    return render_template(
        "copper/supplier_ledger.html",
        supplier=supplier,
        ledger=ledger,
        total_owed=total_owed,
        total_paid=total_paid,
        balance=balance,
        user_role=getattr(current_user, 'role', None),
    )


@core_bp.route("/boss/copper/ledgers")
@role_required("boss", "admin")
def boss_copper_ledgers():
    """Index page for boss/admin to choose copper ledgers.

    Shows distinct customers and suppliers so the boss can click
    through to detailed ledgers without touching accountant routes.
    """
    from copper.models import CopperStock, CopperOutput

    # Query distinct customers and suppliers without loading full objects
    customers_rows = (
        db.session.query(CopperOutput.customer)
        .filter(CopperOutput.customer != None)
        .distinct()
        .order_by(CopperOutput.customer)
        .all()
    )
    customers = [c[0] for c in customers_rows]

    suppliers_rows = (
        db.session.query(CopperStock.supplier)
        .filter(CopperStock.supplier != None)
        .distinct()
        .order_by(CopperStock.supplier)
        .all()
    )
    suppliers = [s[0] for s in suppliers_rows]

    return render_template(
        "boss/copper_ledgers.html",
        customers=customers,
        suppliers=suppliers,
    )


@core_bp.route("/boss/cassiterite/supplier_ledger/<supplier>")
@role_required("boss", "admin")
def boss_cassiterite_supplier_ledger(supplier: str):
    """Boss/admin read-only view of cassiterite supplier ledger.

    Uses the same aggregation logic and template context as the
    accountant-facing cassiterite supplier ledger, but without
    requiring the accountant role.
    """

    from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment

    # All stocks and payments for this supplier
    stocks = (
        CassiteriteStock.query
        .filter_by(supplier=supplier)
        .order_by(CassiteriteStock.date)
        .all()
    )

    payments = (
        CassiteriteSupplierPayment.query
        .join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
        .filter(CassiteriteStock.supplier == supplier)
        .order_by(CassiteriteSupplierPayment.paid_at)
        .all()
    )

    # Build combined ledger entries (purchases = debit, payments = credit)
    ledger_entries = []

    for stock in stocks:
        ledger_entries.append(
            {
                "date": stock.date,
                "description": f"Purchase {stock.voucher_no}",
                "debit": float(stock.balance_to_pay or 0),
                "credit": 0.0,
                "is_payment": False,
            }
        )

    for payment in payments:
        ledger_entries.append(
            {
                "date": payment.paid_at.date() if payment.paid_at else None,
                "description": f"Payment (ref: {payment.reference or 'N/A'})",
                "debit": 0.0,
                "credit": float(payment.amount or 0),
                "is_payment": True,
            }
        )

    # Sort all entries by date
    ledger_entries.sort(key=lambda x: x["date"] or 0)

    # Compute totals using DB aggregates to avoid Python-side summation
    total_owed = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).filter(CassiteriteStock.supplier == supplier).scalar()
    total_paid = db.session.query(func.coalesce(func.sum(CassiteriteSupplierPayment.amount), 0)).join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id).filter(CassiteriteStock.supplier == supplier).scalar()
    balance = (total_owed or 0) - (total_paid or 0)

    return render_template(
        "cassiterite/supplier_ledger.html",
        supplier=supplier,
        ledger_entries=ledger_entries,
        total_owed=total_owed,
        total_paid=total_paid,
        balance=balance,
        user_role=getattr(current_user, 'role', None),
    )


@core_bp.route("/boss/cassiterite/ledgers")
@role_required("boss", "admin")
def boss_cassiterite_ledgers():
    """Index page for boss/admin to choose cassiterite ledgers."""
    from cassiterite.models import CassiteriteStock, CassiteriteOutput

    customers_rows = (
        db.session.query(CassiteriteOutput.customer)
        .filter(CassiteriteOutput.customer != None)
        .distinct()
        .order_by(CassiteriteOutput.customer)
        .all()
    )
    customers = [c[0] for c in customers_rows]

    suppliers_rows = (
        db.session.query(CassiteriteStock.supplier)
        .filter(CassiteriteStock.supplier != None)
        .distinct()
        .order_by(CassiteriteStock.supplier)
        .all()
    )
    suppliers = [s[0] for s in suppliers_rows]

    return render_template(
        "boss/cassiterite_ledgers.html",
        customers=customers,
        suppliers=suppliers,
    )


@core_bp.route("/notifications/mark_read/<int:notification_id>", methods=["POST"])
def mark_notification_read(notification_id: int):
    """Mark a single notification as read for the current user.

    This route is intentionally simple and generic so it can be reused
    from any dashboard (copper, cassiterite, store, boss).
    """
    if not getattr(current_user, "is_authenticated", False):
        # Only logged-in users are allowed to change notification state.
        abort(401)

    notif = Notification.query.get_or_404(notification_id)

    # Safety check: users can only touch their own notifications.
    if notif.user_id != current_user.id:
        abort(403)

    from datetime import datetime as _dt
    notif.read_at = _dt.utcnow()
    db.session.commit()

    # Redirect back to where the user came from (fallback to home).
    return redirect(request.referrer or url_for("entry_point"))


@core_bp.route("/notifications/mark_all_read", methods=["POST"])
def mark_all_notifications_read():
    """Mark all unread notifications for the current user as read.

    Used by dashboards via a single "Mark all as read" button.
    """
    if not getattr(current_user, "is_authenticated", False):
        abort(401)

    from datetime import datetime as _dt
    now = _dt.utcnow()

    (
        Notification.query
        .filter_by(user_id=current_user.id, read_at=None)
        .update({Notification.read_at: now}, synchronize_session=False)
    )
    db.session.commit()

    return redirect(request.referrer or url_for("entry_point"))


# ---------------------------------------------------------------------------
# Admin: user and role management
# ---------------------------------------------------------------------------


@core_bp.route("/admin/users")
@role_required("admin")
def admin_users():
    """List all application users for the admin.

    From here the admin can:
    - See who exists and which role they have
    - Jump to edit screens
    - Deactivate/activate accounts
    - Delete accounts completely
    """

    users = User.query.options(joinedload(User.notifications)).order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users, allowed_roles=ALLOWED_ROLES)


@core_bp.route("/admin/users/new", methods=["GET", "POST"])
@role_required("admin")
def admin_create_user():
    """Create a new user and assign a role.

    We keep validation simple and focused on what matters:
    - username and password are required
    - role must be one of ALLOWED_ROLES
    - username and email must be unique
    """

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        role = (request.form.get("role") or "accountant").strip()
        password = (request.form.get("password") or "").strip()
        is_active = bool(request.form.get("is_active"))

        errors: list[str] = []

        if not username:
            errors.append("Username is required.")
        if not password:
            errors.append("Password is required.")
        if role not in ALLOWED_ROLES:
            errors.append("Invalid role selected.")

        # Uniqueness checks
        if username and User.query.filter_by(username=username).first():
            errors.append("Username is already taken.")
        if email and User.query.filter_by(email=email).first():
            errors.append("Email is already in use.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            # Re-render the form with whatever the user typed
            return render_template(
                "admin/user_form.html",
                mode="create",
                user=None,
                allowed_roles=ALLOWED_ROLES,
                form_data={
                    "username": username,
                    "email": email or "",
                    "role": role,
                    "is_active": is_active,
                },
            )

        # All good: create the user
        new_user = User(
            username=username,
            email=email,
            role=role,
            is_active=is_active,
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        flash("User created successfully.", "success")
        return redirect(url_for("core.admin_users"))

    # GET request: empty form
    return render_template(
        "admin/user_form.html",
        mode="create",
        user=None,
        allowed_roles=ALLOWED_ROLES,
        form_data=None,
    )


@core_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def admin_edit_user(user_id: int):
    """Edit an existing user (role, email, activation, optional password)."""

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip() or None
        role = (request.form.get("role") or user.role).strip()
        is_active = bool(request.form.get("is_active"))
        password = (request.form.get("password") or "").strip()

        errors: list[str] = []

        if not username:
            errors.append("Username is required.")

        if role not in ALLOWED_ROLES:
            errors.append("Invalid role selected.")

        # Uniqueness check for email (if changed)
        if email and email != user.email:
            if User.query.filter(User.email == email, User.id != user.id).first():
                errors.append("Email is already in use.")

        # Uniqueness check for username (if changed)
        if username and username != user.username:
            if User.query.filter(User.username == username, User.id != user.id).first():
                errors.append("Username is already taken.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "admin/user_form.html",
                mode="edit",
                user=user,
                allowed_roles=ALLOWED_ROLES,
                form_data={
                    "username": username or user.username,
                    "email": email or "",
                    "role": role,
                    "is_active": is_active,
                },
            )
        else:
            user.username = username or user.username
            user.email = email
            user.role = role
            user.is_active = is_active
            if password:
                user.set_password(password)
            db.session.commit()
            flash("User updated successfully.", "success")
            return redirect(url_for("core.admin_users"))

    return render_template(
        "admin/user_form.html",
        mode="edit",
        user=user,
        allowed_roles=ALLOWED_ROLES,
        form_data=None,
    )


@core_bp.route("/admin/users/<int:user_id>/toggle_active", methods=["POST"])
@role_required("admin")
def admin_toggle_user_active(user_id: int):
    """Activate or deactivate a user.

    This is a soft way to remove access without deleting data.
    """

    user = User.query.get_or_404(user_id)

    # Avoid deactivating yourself by mistake
    if user.id == getattr(current_user, "id", None):
        flash("You cannot change your own active status.", "warning")
        return redirect(request.referrer or url_for("core.admin_users"))

    user.is_active = not bool(user.is_active)
    db.session.commit()
    flash("User active status updated.", "success")
    return redirect(request.referrer or url_for("core.admin_users"))


@core_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@role_required("admin")
def admin_delete_user(user_id: int):
    """Permanently delete a user account.

    NOTE: In many real systems you might prefer a pure soft-delete,
    but for now we support a hard delete for simplicity.
    """

    user = User.query.get_or_404(user_id)

    # Prevent deleting yourself entirely
    if user.id == getattr(current_user, "id", None):
        flash("You cannot delete your own account.", "warning")
        return redirect(request.referrer or url_for("core.admin_users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(request.referrer or url_for("core.admin_users"))
