"""
Stock Routes
Handles copper stock entry and export functionality
"""
from datetime import datetime
from io import BytesIO
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
import openpyxl
import pandas as pd

from config import db
from copper.models import CopperStock, CopperOutput, SupplierPayment
from copper import copper_bp
from core.models import Notification, create_notification, User
from sqlalchemy.orm import joinedload, selectinload
from flask_login import current_user
from sqlalchemy import func
import logging
from utils import trace_time
import time
from threading import Lock

logger = logging.getLogger(__name__)

# Simple cache for dashboard aggregates to avoid repeated heavy SQL
_AGG_CACHE = {}
_AGG_CACHE_LOCK = Lock()

def _get_dashboard_aggregates(ttl=10):
    try:
        with _AGG_CACHE_LOCK:
            entry = _AGG_CACHE.get('dashboard')
            if entry and (time.time() - entry.get('ts', 0) < entry.get('ttl', ttl)):
                return entry.get('data')
    except Exception:
        pass
    # compute fresh (caller will set cache after computing)
    return None

def _set_dashboard_aggregates(data, ttl=10):
    try:
        with _AGG_CACHE_LOCK:
            _AGG_CACHE['dashboard'] = {'ts': time.time(), 'data': data, 'ttl': ttl}
    except Exception:
        pass


def _parse_date(s):
    """Helper to parse date strings"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        return None


def _compute_cumulative_map(remaining_filters, page_ids):
    """Compute cumulative total_balance for the given page IDs using a
    windowed SQL query. Returns a dict {stock_id: cumulative_total}.
    """
    if not page_ids:
        return {}
    try:
        from sqlalchemy import select

        base = select(
            CopperStock.id.label('id'),
            func.sum(CopperStock.net_balance).over(order_by=(CopperStock.date, CopperStock.id)).label('cumulative')
        ).where(*remaining_filters).cte('ordered')

        q = select(base.c.id, base.c.cumulative).where(base.c.id.in_(page_ids))
        rows = db.session.execute(q).fetchall()
        return {r.id: float(r.cumulative or 0) for r in rows}
    except Exception:
        try:
            logger.exception("_compute_cumulative_map failed")
        except Exception:
            pass
        return {}


@copper_bp.route("/stock/<int:stock_id>/delete", methods=["POST"])
@trace_time
def delete_stock(stock_id):
    """Delete a copper stock and its related outputs/payments, then redirect to dashboard."""
    try:
        logger.info("delete_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CopperStock.query.get_or_404(stock_id)
        voucher = stock.voucher_no
        try:
            # Compute and remove this stock's contribution from the aggregate
            try:
                contrib_q, contrib_wp, contrib_t = CopperStock.contribution(stock)
            except Exception:
                contrib_q = contrib_wp = contrib_t = 0.0

            db.session.delete(stock)
            # Ensure deletion is flushed so downstream reads see the change
            try:
                db.session.flush()
            except Exception:
                pass

            # Apply delta to the single-row aggregate (remove contribution)
            try:
                CopperStock.apply_aggregate_delta(-contrib_q, -contrib_wp, -contrib_t)
            except Exception:
                logger.exception("delete_stock: failed to apply aggregate delta after delete")

            # Invalidate dashboard cache so clients don't read stale aggregates
            try:
                _set_dashboard_aggregates(None, ttl=0)
            except Exception:
                pass

            # Notify all bosses (fetch ids only to avoid hydrating full User objects)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_delete",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} deleted copper stock {voucher}.",
                    related_type="copper_stock",
                    related_id=stock_id
                )

            db.session.commit()
            logger.info("delete_stock: completed id=%s voucher=%s", stock_id, voucher)
            flash(f"Copper stock {voucher} deleted.", "success")
            return redirect(url_for("copper.dashboard"))
        except Exception:
            logger.exception("delete_stock failed id=%s; rolling back", stock_id)
            try:
                db.session.rollback()
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("delete_stock failed id=%s", stock_id)
        raise


@copper_bp.route("/stock/<int:stock_id>/edit", methods=["POST"])
@trace_time
def edit_stock(stock_id):
    """Basic in-place edit for core copper stock fields, recalculating dependent values."""
    try:
        logger.info("edit_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CopperStock.query.get_or_404(stock_id)

        # Parse incoming fields
        date = _parse_date(request.form.get("date")) or stock.date
        voucher = request.form.get("voucher_no") or stock.voucher_no
        supplier = request.form.get("supplier") or stock.supplier
        input_kg = float(request.form.get("input_kg") or stock.input_kg or 0)
        percentage = float(request.form.get("percentage") or stock.percentage or 0)
        nb = float(request.form.get("nb") or stock.nb or 0)
        u_price = float(request.form.get("u_price") or stock.u_price or 0)
        exchange = float(request.form.get("exchange") or stock.exchange or 0)
        transport_tag = float(request.form.get("transport_tag") or stock.transport_tag or 0)
        rra_3_percent_default = float(request.form.get("rra_3_percent_default") or 50)

        # Keep same per-kg RMA/Inkomane rates as before (if any)
        old_input = stock.input_kg or 0
        per_rma = (stock.rma or 0) / old_input if old_input else 125
        per_inkomane = (stock.inkomane or 0) / old_input if old_input else 40

        # Duplicate voucher check if changed
        if voucher != stock.voucher_no:
            existing = CopperStock.query.filter_by(voucher_no=voucher).first()
            if existing:
                flash(f"Voucher number {voucher} already exists.", "error")
                logger.warning("edit_stock: duplicate voucher %s attempted by %s", voucher, getattr(current_user, "username", None))
                return redirect(url_for("copper.dashboard"))

        # Capture old contribution before mutating
        try:
            old_q, old_wp, old_t = CopperStock.contribution(stock)
        except Exception:
            old_q = old_wp = old_t = 0.0

        # Update base fields
        stock.date = date
        stock.voucher_no = voucher
        stock.supplier = supplier
        stock.input_kg = input_kg
        stock.percentage = percentage
        stock.nb = nb
        stock.u_price = u_price
        stock.exchange = exchange
        stock.transport_tag = transport_tag

        # Recompute derived values following add_stock logic
        stock.u = nb * input_kg
        stock.rra = per_rma * input_kg
        stock.inkomane = per_inkomane * input_kg
        stock.amount = percentage * input_kg * exchange * u_price
        stock.tot_amount_tag = transport_tag * input_kg
        stock.rra_3_percent = (rra_3_percent_default * exchange * percentage * input_kg) * 3 / 100

        try:
            stock.update_calculations()

            # Compute new contribution and apply delta to aggregate
            try:
                new_q, new_wp, new_t = CopperStock.contribution(stock)
                delta_q = new_q - (old_q or 0.0)
                delta_wp = new_wp - (old_wp or 0.0)
                delta_t = new_t - (old_t or 0.0)
                CopperStock.apply_aggregate_delta(delta_q, delta_wp, delta_t)
            except Exception:
                logger.exception("edit_stock: failed to apply aggregate delta")

            # Notify all bosses (fetch ids only to avoid hydrating full User objects)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_edit",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} edited copper stock {voucher}.",
                    related_type="copper_stock",
                    related_id=stock_id
                )

            db.session.commit()
            logger.info("edit_stock: completed id=%s voucher=%s", stock_id, voucher)
            flash(f"Copper stock {voucher} updated.", "success")
            return redirect(url_for("copper.dashboard"))
        except Exception:
            logger.exception("edit_stock failed id=%s; rolling back", stock_id)
            try:
                db.session.rollback()
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("edit_stock failed id=%s", stock_id)
        raise


@copper_bp.route("/add_stock", methods=["GET", "POST"])
@trace_time
def add_stock():
    """Add new copper stock entry"""
    from copper.forms import CopperStockForm

    form = CopperStockForm()
    if request.method == "POST":
        try:
            logger.info("add_stock: start user=%s", getattr(current_user, 'username', None))

            date = _parse_date(request.form.get("date")) or datetime.utcnow().date()
            voucher = request.form.get("voucher_no")
            supplier = request.form.get("supplier")
            input_kg = float(request.form.get("input_kg") or 0)
            percentage = float(request.form.get("percentage") or 0)
            nb = float(request.form.get("nb") or 0)
            rma_default = float(request.form.get("rma_default") or 150)
            inkomane_default = float(request.form.get("inkomane_default") or 40)
            u_price = float(request.form.get("u_price") or 0)
            exchange = float(request.form.get("exchange") or 0)
            transport_tag = float(request.form.get("transport_tag") or 0)
            rra_3_percent_default = float(request.form.get("rra_3_percent_default") or 50)

            # Calculate derived fields
            u = nb * input_kg
            rma = rma_default * input_kg
            inkomane = inkomane_default * input_kg
            amount = percentage * input_kg * exchange * u_price
            tot_amount_tag = transport_tag * input_kg
            rra_3_percent = (rra_3_percent_default * exchange * percentage * input_kg) * 3 / 100
            net_balance = (amount or 0) - (tot_amount_tag or 0) - (rma or 0) - (inkomane or 0) - (rra_3_percent or 0)

            # For production we maintain `total_balance` at read-time using a
            # windowed query. Avoid doing a full SUM(...) here — store the
            # per-row net_balance and compute cumulative totals when the
            # frontend requests paged results.
            total_balance = net_balance

            # Check for duplicate voucher
            existing = CopperStock.query.filter_by(voucher_no=voucher).first()
            if existing:
                return jsonify({"error": f"Voucher number {voucher} already exists."}), 400

            # Create stock object
            s = CopperStock(
                date=date,
                voucher_no=voucher,
                supplier=supplier,
                input_kg=input_kg,
                percentage=percentage,
                nb=nb,
                u=u,
                u_price=u_price,
                exchange=exchange,
                transport_tag=transport_tag,
                tot_amount_tag=tot_amount_tag,
                rma=rma,
                inkomane=inkomane,
                amount=amount,
                rra_3_percent=rra_3_percent,
                net_balance=net_balance,
                total_balance=total_balance
            )

            try:
                db.session.add(s)
                db.session.flush()

                s.update_calculations()

                # Apply delta: add this stock's contribution to the single-row aggregate
                try:
                    q, wp, t = CopperStock.contribution(s)
                    CopperStock.apply_aggregate_delta(q, wp, t)
                except Exception:
                    logger.exception("add_stock: failed to apply aggregate delta")

                db.session.commit()
                logger.info("add_stock: completed voucher=%s", voucher)
                flash("Copper stock added successfully!", "success")
                return redirect(url_for("copper.dashboard"))
            except Exception:
                logger.exception("add_stock failed voucher=%s; rolling back", voucher)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                raise
        except Exception:
            logger.exception("add_stock outer failure")
            raise

    return render_template("copper/add_stock.html", form=form)


@copper_bp.route("/dashboard")
def dashboard():
    """Copper dashboard"""
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20
    # Avoid pre-loading related supplier_payments here to reduce hydration cost on dashboard.
    stocks_pagination = CopperStock.query.order_by(CopperStock.date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    stocks = stocks_pagination.items
    outputs = CopperOutput.query.order_by(CopperOutput.date.desc()).limit(10).all()
    # Try to use cached dashboard aggregates when available to keep the
    # initial page render fast. If a cache entry exists use it; otherwise
    # defer heavy aggregate computation to the client (via `/api/filter_stocks`
    # with `include_aggregates=true`) so the server-side template render
    # remains under the interactive threshold.
    # Ensure these are always defined to avoid UnboundLocalError when
    # cached_aggs is present or when the user is not authenticated.
    moyenne = 0
    moyenne_nb = 0
    remaining_stocks_count = 0
    cached_aggs = _get_dashboard_aggregates(ttl=10)
    if cached_aggs:
        total_input = cached_aggs.get('total_input', 0)
        total_output = cached_aggs.get('total_output', 0)
        total_debt = cached_aggs.get('total_debt', 0)
        total_sales = cached_aggs.get('total_sales', 0)
        total_supplier_obligation = cached_aggs.get('total_supplier_obligation', 0)
        copper_inventory_value = cached_aggs.get('inventory_value', 0)
        copper_cost_of_stock_sold = cached_aggs.get('cost_of_stock_sold', 0)
        gross_profit = cached_aggs.get('gross_profit', 0)
        supplier_debt = total_supplier_obligation
        customer_debt = total_debt
        cash_position = cached_aggs.get('cash_position', 0)
        moyenne = cached_aggs.get('moyenne', 0)
        moyenne_nb = cached_aggs.get('moyenne_nb', 0)
    else:
        # Lightweight placeholders - real aggregates are fetched asynchronously
        total_input = 0
        total_output = 0
        total_debt = 0
        total_sales = 0
        total_supplier_obligation = 0
        copper_inventory_value = 0
        copper_cost_of_stock_sold = 0
        gross_profit = 0
        supplier_debt = 0
        customer_debt = 0
        cash_position = 0

    user_notifications = []
    if getattr(current_user, "is_authenticated", False):
        # Show all unread notifications and up to 10 already-read notifications
        # Only fetch a small recent set of unread notifications to avoid heavy hydration
        unread = (
            Notification.query.options(joinedload(Notification.user))
            .filter_by(user_id=current_user.id, read_at=None)
            .order_by(Notification.created_at.desc())
            .limit(20)
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

        # Remaining stocks aggregates (compute counts/aggregates only — avoid hydrating full lists)
        cached = _get_dashboard_aggregates(ttl=10)
        if cached:
            remaining_stocks_count = cached.get('remaining_stocks_count', 0)
            total_unit_percent = cached.get('total_unit_percent', 0)
            total_remaining_balance = cached.get('total_remaining_balance', 0)
            moyenne = cached.get('moyenne', 0)
            total_t_unity = cached.get('total_t_unity', 0)
            moyenne_nb = cached.get('moyenne_nb', 0)
        else:
            remaining_stocks_count = CopperStock.query.filter(CopperStock.local_balance > 0).count()
            total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
            total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
            moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
            total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
            moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
            _set_dashboard_aggregates({
                'remaining_stocks_count': remaining_stocks_count,
                'total_unit_percent': total_unit_percent,
                'total_remaining_balance': total_remaining_balance,
                'moyenne': moyenne,
                'total_t_unity': total_t_unity,
                'moyenne_nb': moyenne_nb,
            }, ttl=10)

        # Ensure server-rendered rows display the global moyenne values (don't rely
        # on per-row stored fields which we may avoid updating on every insert).
        try:
            for s in stocks:
                s.moyenne = moyenne
                s.moyenne_nb = moyenne_nb
        except Exception:
            pass

    return render_template(
        'copper/dashboard.html',
        stocks=stocks,
        # do not pass full remaining_stocks list (avoid loading all rows); template/JS use counts and paged API
        outputs=outputs,
        total_input=total_input,
        total_output=total_output,
        total_debt=total_debt,
        total_sales=total_sales,
        total_supplier_obligation=total_supplier_obligation,
        gross_profit=gross_profit,
        supplier_debt=supplier_debt,
        customer_debt=customer_debt,
        cash_position=cash_position,
        notifications=user_notifications,
        unread_notifications_count=Notification.query.filter_by(user_id=current_user.id, read_at=None).count(),
        moyenne=moyenne,
        moyenne_nb=moyenne_nb,
        remaining_stocks_count=remaining_stocks_count,
        stocks_pagination=stocks_pagination,
        page=page,
        per_page=per_page,
    )


@copper_bp.route("/export_stocks")
def export_stocks():
    """Export all copper stocks to Excel"""
    stocks = CopperStock.query.order_by(CopperStock.date.desc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Copper Stock"

    headers = [
        "Date", "Voucher", "Supplier", "Input (kg)", "Output (kg)", "Local Bal", "Total Local Bal",
        "%", "Moyenne", "NB", "Moyenne NB", "Net Balance", "Total Balance", "RMA", "Inkomane"
    ]
    ws.append(headers)
    for s in stocks:
        # Use the global aggregate for moyenne values (cheaper and consistent)
        try:
            from core.models import StockAggregate
            agg = StockAggregate.get('copper')
            if agg and agg.total_quantity:
                moyenne_val = agg.total_weighted_percent / agg.total_quantity
                moyenne_nb_val = agg.total_t_unity / agg.total_quantity
            else:
                moyenne_val = s.moyenne or 0
                moyenne_nb_val = s.moyenne_nb or 0
        except Exception:
            moyenne_val = s.moyenne or 0
            moyenne_nb_val = s.moyenne_nb or 0

        ws.append([
            s.date.strftime("%Y-%m-%d"),
            s.voucher_no,
            s.supplier,
            s.input_kg,
            s.local_balance,
            s.total_local_balance,
            s.percentage,
            moyenne_val,
            s.nb,
            moyenne_nb_val,
            s.net_balance,
            s.total_balance,
            s.rma,
            s.inkomane
        ])
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out,
                     as_attachment=True,
                     download_name=f"copper_stock_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@copper_bp.route("/export_filtered_stocks")
def export_filtered_stocks():
    """Export filtered copper stocks to Excel"""
    percentage_filter = request.args.get('percentage_filter')
    nb_filter = request.args.get('nb_filter')

    stock = CopperStock.query.order_by(CopperStock.date.desc()).first()
    moyenne = stock.moyenne if stock else 0
    moyenne_nb = stock.moyenne_nb if stock else 0

    all_stock = CopperStock.query.filter(CopperStock.local_balance > 0)

    if percentage_filter == 'above':
        all_stock = all_stock.filter(CopperStock.percentage >= moyenne)
    elif percentage_filter == 'below':
        all_stock = all_stock.filter(CopperStock.percentage <= moyenne)

    if nb_filter == 'above':
        all_stock = all_stock.filter(CopperStock.nb >= moyenne_nb)
    elif nb_filter == 'below':
        all_stock = all_stock.filter(CopperStock.nb <= moyenne_nb)

    filtered_stocks = all_stock.all()

    # Convert to Pandas DataFrame
    df = pd.DataFrame([{
        "Voucher": s.voucher_no,
        "Input_kg": s.input_kg,
        "U": s.u,
        "RMA": s.rma,
        "INKOMANE": s.inkomane,
        "Percentage": s.percentage,
        "Nb": s.nb,
        "Local_Balance": s.local_balance
    } for s in filtered_stocks])

    # Export to Excel in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Filtered Stocks')
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="filtered_copper_stocks.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



@copper_bp.route('/api/filter_stocks', methods=['POST'])
@trace_time
def filter_stocks():
    """Filter stocks by date range (and optional voucher) and return JSON with all recalculated metrics"""
    from flask import request, jsonify
    from datetime import datetime
    try:
        data = request.get_json()
        logger.info("filter_stocks: start user=%s data=%s", getattr(current_user, 'username', None), data)
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        voucher_no = data.get('voucher_no') or None
    
    # Filter stocks
        stocks_query = CopperStock.query.order_by(CopperStock.date.desc())
        
        if start_date:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            stocks_query = stocks_query.filter(CopperStock.date >= start)
        
        if end_date:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            stocks_query = stocks_query.filter(CopperStock.date <= end)

        if voucher_no:
            stocks_query = stocks_query.filter(CopperStock.voucher_no == voucher_no)
        
        # Prefer a meaningful snapshot: only remaining stocks (local_balance > 0)
        # Support pagination from client to avoid returning huge payloads.
        page = int(data.get('page', 1) or 1)
        # Default to a small page size for interactive forms; cap to 10.
        per_page = int(data.get('per_page', 10) or 10)
        if per_page < 5:
            per_page = 5
        if per_page > 10:
            per_page = 10

        # Whether the caller wants heavy aggregates (inventory, COGS, sales aggregates).
        # Dashboard initial load should set this to False to keep page load fast.
        include_aggregates = bool(data.get('include_aggregates', True))

        stocks_local_q = stocks_query.filter(CopperStock.local_balance > 0)
        stocks_pagination = stocks_local_q.paginate(page=page, per_page=per_page, error_out=False)
        filtered_stocks = stocks_pagination.items
        include_all = bool(data.get('include_all'))

        # Safeguard: refuse to return an unbounded large result set unless
        # explicitly requested via export. Protects frontend from freezing.
        MAX_RETURN_ROWS = 2000
        if include_all and stocks_pagination.total > MAX_RETURN_ROWS:
            logger.warning("filter_stocks: include_all requested but total %d > max %d", stocks_pagination.total, MAX_RETURN_ROWS)
            return jsonify({'error': 'Request would return too many rows; please narrow your filter or use the export endpoint.'}), 400

        # Filter outputs by same date range but restrict to only outputs
        # belonging to stocks on the current page to compute per-stock remaining.
        outputs_query = CopperOutput.query.order_by(CopperOutput.date.desc())
        if start_date:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            outputs_query = outputs_query.filter(CopperOutput.date >= start)

        if end_date:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            outputs_query = outputs_query.filter(CopperOutput.date <= end)

        # Restrict outputs to only those referencing stocks on this page to
        # keep the payload small and avoid unnecessary work.
        page_stock_ids = [s.id for s in filtered_stocks]
        if page_stock_ids:
            outputs_query = outputs_query.filter(CopperOutput.stock_id.in_(page_stock_ids))

        filtered_outputs = outputs_query.all()

        # Build a per-stock sum of output_kg to avoid N+1 DB calls when
        # computing remaining per-stock. Aggregate in Python (one pass) and reuse below.
        from collections import defaultdict
        import time
        timings = {}
        outputs_sums = defaultdict(float)
        for o in filtered_outputs:
            if o and o.stock_id:
                outputs_sums[o.stock_id] += float(o.output_kg or 0)

        # Default aggregate values to avoid UnboundLocalError if computation fails
        total_input = 0
        total_stocks = stocks_pagination.total
        total_output = 0
        total_debt = 0
        total_sales = 0
        total_supplier_obligation = 0
        total_payments = 0
        inventory_value = 0
        cost_of_stock_sold = 0
        gross_profit = 0
        supplier_outstanding = 0
        total_unit_percent = 0
        total_remaining_balance = 0
        moyenne = 0
        total_t_unity = 0
        moyenne_nb = 0

        # Start timing for DB aggregates
        if include_aggregates:
            # If caller requested the global dashboard aggregates (no date/voucher filters)
            # try using the in-process cache to avoid repeating heavy SQL.
            use_cache_key = not (start_date or end_date or voucher_no)
            cached_aggs = None
            # If caller requested full aggregates, compute fresh values instead
            # of relying on any possibly-partial cache entry. This avoids
            # returning stale zeroed aggregates when the cache was only seeded
            # with lightweight values (e.g., moyenne).
            if use_cache_key and not include_aggregates:
                cached_aggs = _get_dashboard_aggregates()

            if cached_aggs:
                # If the caller requested full aggregates, ensure the cached
                # entry contains the heavy financial keys; otherwise ignore
                # this cache entry so we compute the full aggregates now.
                heavy_keys = ('inventory_value', 'cost_of_stock_sold', 'total_sales', 'total_supplier_obligation')
                if include_aggregates and not all(k in cached_aggs for k in heavy_keys):
                    # treat as cache miss for full-aggregate request
                    cached_aggs = None
                else:
                    # Use cached aggregate values and avoid running heavy queries
                    total_input = cached_aggs.get('total_input', 0)
                    total_stocks = cached_aggs.get('total_stocks', stocks_pagination.total)
                    total_output = cached_aggs.get('total_output', 0)
                    total_debt = cached_aggs.get('total_debt', 0)
                    total_sales = cached_aggs.get('total_sales', 0)
                    total_supplier_obligation = cached_aggs.get('total_supplier_obligation', 0)
                    total_payments = cached_aggs.get('total_payments', 0)
                    inventory_value = cached_aggs.get('inventory_value', 0)
                    cost_of_stock_sold = cached_aggs.get('cost_of_stock_sold', 0)
                    supplier_outstanding = cached_aggs.get('supplier_outstanding', 0)
                    gross_profit = cached_aggs.get('gross_profit', 0)
                    total_unit_percent = cached_aggs.get('total_unit_percent', 0)
                    total_remaining_balance = cached_aggs.get('total_remaining_balance', 0)
                    moyenne = cached_aggs.get('moyenne', 0)
                    total_t_unity = cached_aggs.get('total_t_unity', 0)
                    moyenne_nb = cached_aggs.get('moyenne_nb', 0)
                    timings['stock_aggregates'] = 0.0
                    timings['output_aggregates'] = 0.0
                    timings['sales_aggregate'] = 0.0
                    timings['supplier_obligation'] = 0.0
                    timings['payments_aggregate'] = 0.0
                    timings['inventory_value'] = 0.0
                    timings['cogs_aggregate'] = 0.0
                    timings['remaining_aggregates'] = 0.0
            else:
                t0 = time.perf_counter()
                # stock_filters represent the "original" cost basis window (what we
                # ordered from suppliers in this filtered period).
                stock_filters = []
                if start_date:
                    stock_filters.append(CopperStock.date >= start)
                if end_date:
                    stock_filters.append(CopperStock.date <= end)
                if voucher_no:
                    stock_filters.append(CopperStock.voucher_no == voucher_no)

                total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).filter(*stock_filters).scalar() or 0
                total_stocks = db.session.query(func.coalesce(func.count(CopperStock.id), 0)).filter(*stock_filters).scalar() or 0
                timings['stock_aggregates'] = time.perf_counter() - t0

                output_filters = []
                if start_date:
                    output_filters.append(CopperOutput.date >= start)
                if end_date:
                    output_filters.append(CopperOutput.date <= end)

                t1 = time.perf_counter()
                total_output = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).filter(*output_filters).scalar() or 0
                total_debt = db.session.query(func.coalesce(func.sum(CopperOutput.debt_remaining), 0)).filter(*output_filters).scalar() or 0
                timings['output_aggregates'] = time.perf_counter() - t1

                # Total sales (monetary) for the filtered outputs window
                t2 = time.perf_counter()
                total_sales = db.session.query(func.coalesce(func.sum(CopperOutput.output_amount), 0)).filter(*output_filters).scalar() or 0
                timings['sales_aggregate'] = time.perf_counter() - t2

                # Total supplier obligation (net_balance) respecting the same stock filters.
                t3 = time.perf_counter()
                total_supplier_obligation = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(*stock_filters).scalar() or 0
                timings['supplier_obligation'] = time.perf_counter() - t3

                # Total payments made against the filtered stocks
                t4 = time.perf_counter()
                total_payments = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).join(CopperStock, SupplierPayment.stock_id == CopperStock.id).filter(*stock_filters).scalar() or 0
                timings['payments_aggregate'] = time.perf_counter() - t4

                # Remaining stocks aggregates (only local_balance > 0)
                remaining_filters = list(stock_filters) + [CopperStock.local_balance > 0]

                remaining_value_filters = list(remaining_filters) + [CopperStock.input_kg > 0]
                try:
                    t5 = time.perf_counter()
                    inventory_value = db.session.query(
                        func.coalesce(
                            func.sum(CopperStock.net_balance * CopperStock.local_balance / CopperStock.input_kg),
                            0,
                        )
                    ).filter(*remaining_value_filters).scalar() or 0
                    timings['inventory_value'] = time.perf_counter() - t5
                except Exception:
                    logger.exception('filter_stocks: inventory_value aggregate failed')
                    inventory_value = 0
                    timings['inventory_value'] = None

                supplier_outstanding = (total_supplier_obligation or 0) - (total_payments or 0)

                try:
                    t6 = time.perf_counter()
                    cogs_q = db.session.query(
                        func.coalesce(
                            func.sum(
                                CopperOutput.output_kg * (CopperStock.net_balance / func.nullif(CopperStock.input_kg, 0))
                            ),
                            0.0,
                        )
                    ).join(CopperStock, CopperOutput.stock_id == CopperStock.id)

                    if output_filters:
                        for f in output_filters:
                            cogs_q = cogs_q.filter(f)

                    cost_of_stock_sold = float(cogs_q.scalar() or 0.0)
                    timings['cogs_aggregate'] = time.perf_counter() - t6
                except Exception:
                    logger.exception("Failed to compute COGS from outputs; falling back to purchases-minus-closing")
                    cost_of_stock_sold = (total_supplier_obligation or 0) - (inventory_value or 0)
                    timings['cogs_aggregate'] = None

                gross_profit = (total_sales or 0) - (cost_of_stock_sold or 0)
                t7 = time.perf_counter()
                total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
                total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
                timings['remaining_aggregates'] = time.perf_counter() - t7
                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(*remaining_filters).scalar() or 0
                moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0

                # Cache global dashboard aggregates for a short TTL to speed up
                # repeated dashboard loads in development and small deployments.
                if use_cache_key:
                    try:
                        _set_dashboard_aggregates({
                            'total_input': total_input,
                            'total_output': total_output,
                            'total_debt': total_debt,
                            'total_sales': total_sales,
                            'total_supplier_obligation': total_supplier_obligation,
                            'total_payments': total_payments,
                            'inventory_value': inventory_value,
                            'cost_of_stock_sold': cost_of_stock_sold,
                            'supplier_outstanding': supplier_outstanding,
                            'gross_profit': gross_profit,
                            'total_stocks': total_stocks,
                            'total_unit_percent': total_unit_percent,
                            'total_remaining_balance': total_remaining_balance,
                            'moyenne': moyenne,
                            'total_t_unity': total_t_unity,
                            'moyenne_nb': moyenne_nb,
                            'cash_position': (total_sales or 0) - (total_debt or 0),
                        }, ttl=10)
                    except Exception:
                        logger.exception('Failed to set dashboard aggregates cache')
        else:
            # Caller asked for a lightweight page (no heavy aggregates).
            total_input = 0
            total_stocks = stocks_pagination.total
            total_output = 0
            total_debt = 0
            total_sales = 0
            total_supplier_obligation = 0
            total_payments = 0
            inventory_value = 0
            cost_of_stock_sold = 0
            gross_profit = 0
            supplier_outstanding = 0
            total_unit_percent = 0
            total_remaining_balance = 0
            moyenne = 0
            total_t_unity = 0
            moyenne_nb = 0
            timings['stock_aggregates'] = 0.0
            timings['output_aggregates'] = 0.0
            timings['sales_aggregate'] = 0.0
            timings['supplier_obligation'] = 0.0
            timings['payments_aggregate'] = 0.0
            timings['inventory_value'] = 0.0
            timings['cogs_aggregate'] = 0.0
            timings['remaining_aggregates'] = 0.0
        # Defer final timing and logging until after we build the response payload
        
        # Build stocks data for table (measure time so we can see where the remainder is spent)
        # Compute page cumulative totals using a windowed query so we don't
        # persist expensive per-insert updates for `total_balance`.
        cumulative_map = _compute_cumulative_map(remaining_filters, page_stock_ids)
        t_build = time.perf_counter()
        stocks_data = []
        for stock in filtered_stocks:
            stocks_data.append({
                'id': stock.id,
                'date': stock.date.strftime('%Y-%m-%d'),
                'voucher_no': stock.voucher_no,
                'supplier': stock.supplier,
                'input_kg': round(stock.input_kg or 0, 2),
                'percentage': round(stock.percentage or 0, 2),
                'nb': round(stock.nb or 0, 2),
                'u_price': round(stock.u_price or 0, 2),
                'amount': round(stock.amount or 0, 2),
                'exchange': round(stock.exchange or 0, 2),
                'transport_tag': round(stock.transport_tag or 0, 2),
                'rma': round(stock.rma or 0, 2),
                'inkomane': round(stock.inkomane or 0, 2),
                'local_balance': round(stock.local_balance or 0, 2),
                'unit_percent': round(stock.unit_percent or 0, 4),
                't_unity': round(stock.t_unity or 0, 2),
                'rra_3_percent': round(stock.rra_3_percent or 0, 4),
                'net_balance': round(stock.net_balance or 0, 2),
                'total_balance': round(cumulative_map.get(stock.id, stock.total_balance or 0), 2),
                # Use pre-aggregated outputs per-stock to compute remaining
                'remaining': round(((stock.input_kg or 0) - outputs_sums.get(stock.id, 0)) or 0, 2),
                'moyenne': round(moyenne or 0, 4),
                'moyenne_nb': round(moyenne_nb or 0, 4)
            })
        build_rows_time = time.perf_counter() - t_build

        # Build outputs data for charts (date vs output_kg)
        t_build_out = time.perf_counter()
        outputs_data = []
        for output in filtered_outputs:
            outputs_data.append({
                'date': output.date.strftime('%Y-%m-%d'),
                'output_kg': round(output.output_kg or 0, 2)
            })
        build_outputs_time = time.perf_counter() - t_build_out

        # Measure JSON response construction time
        t_json = time.perf_counter()
        payload = {
            'stocks': stocks_data,
            'outputs': outputs_data,
            'page': page,
            'per_page': per_page,
            'pages': stocks_pagination.pages,
            'total': stocks_pagination.total,
            'total_input': round(total_input, 2),
            'total_output': round(total_output, 2),
            'total_debt': round(total_debt, 2),
            # Added financial aggregates for the filtered window
            'total_sales': round(total_sales, 2),
            'total_supplier_obligation': round(total_supplier_obligation, 2),
            'inventory_value': round(inventory_value, 2),
            'cost_of_stock_sold': round(cost_of_stock_sold, 2),
            'total_payments': round(total_payments, 2),
            'supplier_outstanding': round(supplier_outstanding, 2),
            'gross_profit': round(gross_profit, 2),
            'total_stocks': total_stocks,
            'moyenne': round(moyenne, 4),
            'moyenne_nb': round(moyenne_nb, 4)
        }
        try:
            from flask import jsonify
            resp = jsonify(payload)
        except Exception:
            # fallback to manual json build if jsonify fails
            import json
            resp = json.dumps(payload)
        json_time = time.perf_counter() - t_json

        # Finalize timings and log a full breakdown
        timings['build_rows'] = build_rows_time
        timings['build_outputs'] = build_outputs_time
        timings['jsonify'] = json_time
        timings['total_time'] = sum([v for v in timings.values() if isinstance(v, float)])
        logger.info('filter_stocks timings: %s', timings)

        logger.info("filter_stocks: completed stocks=%d outputs=%d page=%d", len(filtered_stocks), len(filtered_outputs), page)
        return resp
    except Exception:
        logger.exception("filter_stocks failed")
        return jsonify({'error': 'internal server error'}), 500
