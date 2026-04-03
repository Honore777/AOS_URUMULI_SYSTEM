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

logger = logging.getLogger(__name__)


def _parse_date(s):
    """Helper to parse date strings"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except:
        return None


@copper_bp.route("/stock/<int:stock_id>/delete", methods=["POST"])
@trace_time
def delete_stock(stock_id):
    """Delete a copper stock and its related outputs/payments, then redirect to dashboard."""
    try:
        logger.info("delete_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CopperStock.query.get_or_404(stock_id)
        voucher = stock.voucher_no
        try:
            db.session.delete(stock)

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

            # Compute rolling total using a SQL aggregate (faster than pulling all rows into Python)
            previous_total_balance = db.session.query(
                func.coalesce(func.sum(CopperStock.net_balance), 0)
            ).filter(CopperStock.date <= date).scalar()
            total_balance = previous_total_balance + net_balance

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
    stocks_pagination = CopperStock.query.options(selectinload(CopperStock.supplier_payments)).order_by(CopperStock.date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    stocks = stocks_pagination.items
    outputs = CopperOutput.query.order_by(CopperOutput.date.desc()).limit(10).all()

    total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).scalar()
    total_output = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).scalar()
    total_debt = db.session.query(func.coalesce(func.sum(CopperOutput.debt_remaining), 0)).scalar()
    total_sales = db.session.query(func.coalesce(func.sum(CopperOutput.output_amount), 0)).scalar()
    total_supplier_obligation = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).scalar()
    # Inventory Value (current cost of remaining Coltan stock)
    copper_inventory_value = db.session.query(
        func.coalesce(
            func.sum(CopperStock.net_balance * CopperStock.local_balance / CopperStock.input_kg),
            0,
        )
    ).filter(CopperStock.local_balance > 0, CopperStock.input_kg > 0).scalar() or 0

    # Cost of stock sold (COGS) = purchases (original supplier obligation)
    # minus closing stock value (inventory_value). Then gross profit = Sales - COGS.
    copper_cost_of_stock_sold = (total_supplier_obligation or 0) - (copper_inventory_value or 0)
    gross_profit = (total_sales or 0) - (copper_cost_of_stock_sold or 0)

    supplier_debt = total_supplier_obligation
    customer_debt = total_debt

    # Cash position for the Copper dashboard should represent cash at hand
    # from sales minus outstanding customer debts (what's actually received).
    cash_position = (total_sales or 0) - (customer_debt or 0)

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

    # Remaining stocks aggregates (compute counts/aggregates only — avoid hydrating full lists)
    remaining_stocks_count = CopperStock.query.filter(CopperStock.local_balance > 0).count()
    total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
    total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
    moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
    total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
    moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0

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
        ws.append([
            s.date.strftime("%Y-%m-%d"),
            s.voucher_no,
            s.supplier,
            s.input_kg,
            s.local_balance,
            s.total_local_balance,
            s.percentage,
            s.moyenne,
            s.nb,
            s.moyenne_nb,
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
        per_page = int(data.get('per_page', 20) or 20)

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
        outputs_sums = defaultdict(float)
        for o in filtered_outputs:
            if o and o.stock_id:
                outputs_sums[o.stock_id] += float(o.output_kg or 0)
    
            import time
            # Aggregates from DB (avoid loading large lists into Python).
            timings = {}
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
        # This is the original cost basis for these lots (what we owe initially
        # before any supplier payments are recorded).
        t3 = time.perf_counter()
        total_supplier_obligation = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(*stock_filters).scalar() or 0
        timings['supplier_obligation'] = time.perf_counter() - t3

        # Total payments made against the filtered stocks (all amounts already
        # approved/recorded by the accountant against these lots).
        t4 = time.perf_counter()
        total_payments = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).join(CopperStock, SupplierPayment.stock_id == CopperStock.id).filter(*stock_filters).scalar() or 0
        timings['payments_aggregate'] = time.perf_counter() - t4

        # Remaining stocks aggregates (only local_balance > 0). We re-use this
        # both for moyenne and for computing the current Inventory Value
        # (agaciro ka stock isigaye mu bubiko).
        remaining_filters = list(stock_filters) + [CopperStock.local_balance > 0]

        # Inventory Value (current cost of remaining stock).
        # Per lot: cost_per_kg = net_balance / input_kg, then
        # current_value = cost_per_kg * local_balance.
        # Implemented in SQL as SUM(net_balance * local_balance / input_kg)
        # and restricted to lots with positive input_kg to avoid
        # division-by-zero issues.
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

        # Supplier outstanding (liability) is based on the original supplier
        # obligation minus all payments recorded for these lots. It does NOT
        # depend on how much stock is still physically remaining.
        supplier_outstanding = (total_supplier_obligation or 0) - (total_payments or 0)

        # Cost of stock sold (COGS) for the filtered window: compute from
        # recorded outputs linked to lots. For each output row we compute
        # cost = output_kg * (stock.net_balance / NULLIF(stock.input_kg,0)).
        # This counts only goods actually removed, avoiding the issue where
        # new purchases inflate COGS when they haven't been sold yet.
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

            # apply date filters when present
            if output_filters:
                for f in output_filters:
                    cogs_q = cogs_q.filter(f)

            cost_of_stock_sold = float(cogs_q.scalar() or 0.0)
            timings['cogs_aggregate'] = time.perf_counter() - t6
        except Exception:
            logger.exception("Failed to compute COGS from outputs; falling back to purchases-minus-closing")
            cost_of_stock_sold = (total_supplier_obligation or 0) - (inventory_value or 0)
            timings['cogs_aggregate'] = None
        # Gross profit for the filtered window should be Sales - COGS.
        gross_profit = (total_sales or 0) - (cost_of_stock_sold or 0)
        t7 = time.perf_counter()
        total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
        total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
        timings['remaining_aggregates'] = time.perf_counter() - t7
        moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
        total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(*remaining_filters).scalar() or 0
        moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
        timings['total_time'] = sum([v for v in timings.values() if isinstance(v, float)])
        logger.info('filter_stocks timings: %s', timings)
        
        # Build stocks data for table
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
                'total_balance': round(stock.total_balance or 0, 2),
                # Use pre-aggregated outputs per-stock to compute remaining
                'remaining': round(((stock.input_kg or 0) - outputs_sums.get(stock.id, 0)) or 0, 2),
                'moyenne': round(stock.moyenne or 0, 4),
                'moyenne_nb': round(stock.moyenne_nb or 0, 4)
            })

    # Build outputs data for charts (date vs output_kg)
        outputs_data = []
        for output in filtered_outputs:
            outputs_data.append({
                'date': output.date.strftime('%Y-%m-%d'),
                'output_kg': round(output.output_kg or 0, 2)
            })

        logger.info("filter_stocks: completed stocks=%d outputs=%d page=%d", len(filtered_stocks), len(filtered_outputs), page)
        return jsonify({
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
        })
    except Exception:
        logger.exception("filter_stocks failed")
        return jsonify({'error': 'internal server error'}), 500
