"""Cassiterite Stock Routes.

This module handles:
- Creating cassiterite stock entries
- Rendering the cassiterite dashboard (with KPIs)
    including optional notifications for the logged-in user.
"""
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, jsonify
from config import db
from cassiterite.models import CassiteriteStock, CassiteriteOutput, CassiteriteSupplierPayment, CassiteriteAdvanceAllocation
from core.models import BulkOutputPlan, BulkPlanStatus, CustomerReceipt
from sqlalchemy import func
from cassiterite.forms import AddCassiteriteStockForm
from cassiterite.routes import cassiterite_bp
from core.auth import role_required
from core.models import Notification, create_notification, User, fetch_user_notifications
from sqlalchemy.orm import joinedload, selectinload
from flask_login import current_user
from utils import trace_time
import logging
logger= logging.getLogger(__name__)

@role_required("accountant")
@trace_time
@cassiterite_bp.route('/add_stock', methods=['GET', 'POST'])
def add_stock():
    """Add new cassiterite stock"""
    form = AddCassiteriteStockForm()
    try:
        logger.info("cassiterite.add_stock: start user=%s", getattr(current_user, "username", None))

        # Populate available advance-payment choices (unallocated advances only).
        try:
            advance_rows = (
                db.session.query(
                    CassiteriteSupplierPayment.id,
                    CassiteriteSupplierPayment.supplier_name,
                    CassiteriteSupplierPayment.advance_remaining,
                    CassiteriteSupplierPayment.paid_at,
                )
                .filter(
                    CassiteriteSupplierPayment.is_advance.is_(True),
                    CassiteriteSupplierPayment.is_deleted.is_(False),
                    CassiteriteSupplierPayment.advance_remaining > 0,
                )
                .order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
                .all()
            )
        except Exception:
            advance_rows = []

        advance_choices = [
            (
                int(row.id),
                f"{(row.supplier_name or 'Unknown supplier')} - Advance remaining: {float(row.advance_remaining or 0):,.2f} RWF",
            )
            for row in advance_rows
        ]
        form.advance_payment_ids.choices = advance_choices

        if form.validate_on_submit():
            # Check if voucher already exists
            existing = CassiteriteStock.query.filter_by(voucher_no=form.voucher_no.data).first()
            if existing:
                logger.warning("cassiterite.add_stock: duplicate voucher %s by %s", form.voucher_no.data, getattr(current_user, "username", None))
                flash(f"Voucher {form.voucher_no.data} already exists!", "error")
                return redirect(url_for('cassiterite.add_stock'))

            # Create new stock
            stock = CassiteriteStock(
                date=form.date.data,
                voucher_no=form.voucher_no.data,
                supplier=form.supplier.data,
                input_kg=form.input_kg.data,
                percentage=form.percentage.data,
                lme=form.lme.data,
                m_lme=form.m_lme.data,
                sec=form.sec.data,
                tc=form.tc.data,
                exchange=form.exchange.data,
                transport_tag=form.transport_tag.data,
                rma=form.rma.data,
                inkomane=form.inkomane.data
            )

            # Run DB-side calculations on the new stock
            stock.update_calculations()

            # Apply selected supplier advances (optional)
            requested_advance_ids = []
            try:
                requested_advance_ids = [int(x) for x in (form.advance_payment_ids.data or [])]
            except Exception:
                requested_advance_ids = []

            try:
                db.session.add(stock)
                db.session.flush()

                if requested_advance_ids:
                    # Lock selected advances and apply them to this stock.
                    advance_payments = (
                        CassiteriteSupplierPayment.query
                        .filter(
                            CassiteriteSupplierPayment.id.in_(requested_advance_ids),
                            CassiteriteSupplierPayment.is_deleted.is_(False),
                            CassiteriteSupplierPayment.is_advance.is_(True),
                            CassiteriteSupplierPayment.advance_remaining > 0,
                        )
                        .with_for_update()
                        .order_by(CassiteriteSupplierPayment.paid_at.asc(), CassiteriteSupplierPayment.id.asc())
                        .all()
                    )

                    if len(advance_payments) != len(set(requested_advance_ids)):
                        flash("One or more selected advances are no longer available.", "danger")
                        db.session.rollback()
                        return render_template('cassiterite/add_entry.html', form=form)

                    total_allocated = 0.0
                    for advance_payment in advance_payments:
                        # Supplier safety: only allow applying advances that belong to the same supplier.
                        adv_supplier = (advance_payment.supplier_name or '').strip().lower()
                        if adv_supplier and adv_supplier != (stock.supplier or '').strip().lower():
                            flash("Selected advances must belong to the same supplier as the stock.", "danger")
                            db.session.rollback()
                            return render_template('cassiterite/add_entry.html', form=form)

                        if total_allocated >= float(stock.balance_to_pay or 0.0):
                            break

                        available = float(advance_payment.advance_remaining or 0.0)
                        if available <= 0:
                            continue

                        remaining_for_stock = max(float(stock.balance_to_pay or 0.0) - total_allocated, 0.0)
                        if remaining_for_stock <= 0:
                            continue

                        apply_amount = min(available, remaining_for_stock)
                        if apply_amount <= 0:
                            continue

                        total_allocated += apply_amount
                        advance_payment.advance_remaining = max(available - apply_amount, 0.0)
                        db.session.add(CassiteriteAdvanceAllocation(
                            stock_id=stock.id,
                            supplier_payment_id=advance_payment.id,
                            applied_amount=apply_amount,
                        ))

                    if total_allocated <= 0:
                        flash("Selected advances could not be applied to this stock.", "danger")
                        db.session.rollback()
                        return render_template('cassiterite/add_entry.html', form=form)

                # Apply delta to global aggregate (add new stock's contribution)
                try:
                    q, wp, t = CassiteriteStock.contribution(stock)
                    CassiteriteStock.apply_aggregate_delta(q, wp, t)
                except Exception:
                    logger.exception("cassiterite.add_stock: failed to apply aggregate delta")

                db.session.commit()
                logger.info("cassiterite.add_stock: completed voucher=%s id=%s", stock.voucher_no, getattr(stock, 'id', None))
                flash(f"Cassiterite stock {stock.voucher_no} added successfully!", "success")
                return redirect(url_for('cassiterite.dashboard'))
            except Exception:
                logger.exception("cassiterite.add_stock failed commit; rolling back")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                raise

        return render_template('cassiterite/add_entry.html', form=form)
    except Exception:
        logger.exception("cassiterite.add_stock failed")
        raise


@role_required("accountant")
@trace_time
@cassiterite_bp.route('/stock/<int:stock_id>/delete', methods=['POST'])
def delete_stock(stock_id):
    """Soft-delete a cassiterite stock and redirect to dashboard."""
    try:
        logger.info("cassiterite.delete_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CassiteriteStock.query.get_or_404(stock_id)
        voucher = stock.voucher_no
        try:
            # Compute and remove this stock's contribution from the aggregate
            try:
                contrib_q, contrib_wp, contrib_t = CassiteriteStock.contribution(stock)
            except Exception:
                contrib_q = contrib_wp = contrib_t = 0.0

            stock.is_deleted = True
            stock.deleted_at = datetime.utcnow()
            stock.deleted_by_id = getattr(current_user, 'id', None)
            stock.delete_reason = request.form.get('delete_reason') or 'Deleted from dashboard.'
            db.session.add(stock)

            # Apply delta to the single-row aggregate (remove contribution)
            try:
                CassiteriteStock.apply_aggregate_delta(-contrib_q, -contrib_wp, -contrib_t)
            except Exception:
                logger.exception("cassiterite.delete_stock: failed to apply aggregate delta after delete")

            # Notify all bosses (ids only)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_delete",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} deleted cassiterite stock {voucher}.",
                    related_type="cassiterite_stock",
                    related_id=stock_id
                )

            db.session.commit()
            logger.info("cassiterite.delete_stock: completed id=%s voucher=%s", stock_id, voucher)
            flash(f"Cassiterite stock {voucher} deleted.", "success")
            return redirect(url_for('cassiterite.dashboard'))
        except Exception:
            logger.exception("cassiterite.delete_stock failed id=%s; rolling back", stock_id)
            try:
                db.session.rollback()
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("cassiterite.delete_stock failed id=%s", stock_id)
        raise


@role_required("accountant")
@trace_time
@cassiterite_bp.route('/stock/<int:stock_id>/edit', methods=['POST'])
def edit_stock(stock_id):
    """Basic in-place edit for core cassiterite stock fields, then recalculate all derived values."""
    try:
        logger.info("cassiterite.edit_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CassiteriteStock.query.get_or_404(stock_id)

        from datetime import datetime as _dt2

        date_raw = request.form.get('date')
        try:
            date_val = _dt2.strptime(date_raw, '%Y-%m-%d').date() if date_raw else stock.date
        except Exception:
            date_val = stock.date

        voucher = request.form.get('voucher_no') or stock.voucher_no
        supplier = request.form.get('supplier') or stock.supplier
        input_kg = float(request.form.get('input_kg') or stock.input_kg or 0)
        percentage = float(request.form.get('percentage') or stock.percentage or 0)
        lme = float(request.form.get('lme') or stock.lme or 0)
        m_lme = float(request.form.get('m_lme') or stock.m_lme or 0)
        sec = float(request.form.get('sec') or stock.sec or 0)
        tc = float(request.form.get('tc') or stock.tc or 0)
        exchange = float(request.form.get('exchange') or stock.exchange or 0)
        transport_tag = float(request.form.get('transport_tag') or stock.transport_tag or 0)

        # Handle duplicate voucher if changed
        if voucher != stock.voucher_no:
            existing = CassiteriteStock.query.filter_by(voucher_no=voucher).first()
            if existing:
                logger.warning("cassiterite.edit_stock: duplicate voucher %s attempted by %s", voucher, getattr(current_user, "username", None))
                flash(f"Lot/voucher number {voucher} already exists.", "error")
                return redirect(url_for('cassiterite.dashboard'))

        # Capture old contribution before mutating
        try:
            old_q, old_wp, old_t = CassiteriteStock.contribution(stock)
        except Exception:
            old_q = old_wp = old_t = 0.0

        # Update base fields
        stock.date = date_val
        stock.voucher_no = voucher
        stock.supplier = supplier
        stock.input_kg = input_kg
        stock.percentage = percentage
        stock.lme = lme
        stock.m_lme = m_lme
        stock.sec = sec
        stock.tc = tc
        stock.exchange = exchange
        stock.transport_tag = transport_tag

        # Recompute derived values using DB-side aggregates
        try:
            stock.update_calculations()

            # Compute new contribution and apply delta to aggregate
            try:
                new_q, new_wp, new_t = CassiteriteStock.contribution(stock)
                delta_q = new_q - (old_q or 0.0)
                delta_wp = new_wp - (old_wp or 0.0)
                delta_t = new_t - (old_t or 0.0)
                CassiteriteStock.apply_aggregate_delta(delta_q, delta_wp, delta_t)
            except Exception:
                logger.exception("cassiterite.edit_stock: failed to apply aggregate delta")

            # Notify all bosses (ids only)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_edit",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} edited cassiterite stock {voucher}.",
                    related_type="cassiterite_stock",
                    related_id=stock_id
                )

            db.session.commit()
            logger.info("cassiterite.edit_stock: completed id=%s voucher=%s", stock_id, voucher)
            flash(f"Cassiterite stock {voucher} updated.", "success")
            return redirect(url_for('cassiterite.dashboard'))
        except Exception:
            logger.exception("cassiterite.edit_stock failed id=%s; rolling back", stock_id)
            try:
                db.session.rollback()
            except Exception:
                pass
            raise
    except Exception:
        logger.exception("cassiterite.edit_stock failed id=%s", stock_id)
        raise


@role_required("accountant")
@cassiterite_bp.route('/dashboard')
@trace_time
def dashboard():
    """Cassiterite dashboard"""
    try:
        # Ensure a fresh session/connection to avoid InFailedSqlTransaction
        # on pooled connections that previously errored.
        try:
            db.session.remove()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        from cassiterite.models import CassiteriteOutput
        
        # Pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = 20
        # load related supplier payments via the model relationship named `payments`
        stocks_pagination = CassiteriteStock.query.options(selectinload(CassiteriteStock.payments)).order_by(CassiteriteStock.date.desc()).paginate(page=page, per_page=per_page, error_out=False)
        stocks = stocks_pagination.items
        outputs = CassiteriteOutput.query.order_by(CassiteriteOutput.date.desc()).limit(10).all()

        # Compute a small distinct list of voucher/lot choices to populate the
        # filter dropdown without materializing large lists in the template.
        try:
            voucher_q = db.session.query(CassiteriteStock.voucher_no).filter(CassiteriteStock.local_balance > 0).distinct().order_by(CassiteriteStock.date.desc()).limit(200)
            voucher_choices = [v for (v,) in voucher_q.all() if v]
        except Exception:
            try:
                voucher_choices = [s.voucher_no for s in stocks if getattr(s, 'voucher_no', None)]
            except Exception:
                voucher_choices = []

        from sqlalchemy import func
        try:
            total_input = db.session.query(func.coalesce(func.sum(CassiteriteStock.input_kg), 0)).scalar()
            total_output = db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_kg), 0)).scalar()
            # Sales must be in sync with customer ledger truth (plans), because
            # Output rows may exist without monetary fields populated.
            total_sales = (
                db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                .filter(
                    BulkOutputPlan.mineral_type.in_(['cassiterite']),
                    BulkOutputPlan.total_expected_amount.isnot(None),
                    BulkOutputPlan.total_expected_amount > 0,
                    BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                )
                .scalar()
            )
            total_supplier_obligation = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).scalar()

            # Customer outstanding debt from single source of truth: plans - receipts
            total_expected_amount = (
                db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                .filter(
                    BulkOutputPlan.mineral_type.in_(['cassiterite']),
                    BulkOutputPlan.total_expected_amount.isnot(None),
                    BulkOutputPlan.total_expected_amount > 0,
                    BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                )
                .scalar()
                or 0.0
            )
            total_paid_amount = (
                db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
                .filter(CustomerReceipt.mineral_type.in_(['cassiterite']))
                .scalar()
                or 0.0
            )
            total_debt = float(total_expected_amount or 0.0) - float(total_paid_amount or 0.0)
            # Inventory Value (current cost of remaining Cassiterite stock)
            cass_inventory_value = db.session.query(
                func.coalesce(
                    func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / CassiteriteStock.input_kg),
                    0,
                )
            ).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.input_kg > 0).scalar() or 0
        except Exception:
            logger.exception("cassiterite.dashboard: aggregate queries failed; resetting session and falling back to safe defaults")
            try:
                db.session.remove()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
            total_input = total_output = total_debt = total_sales = total_supplier_obligation = cass_inventory_value = 0

        # COGS = purchases - closing stock value; gross profit = sales - COGS
        cass_cost_of_stock_sold = (total_supplier_obligation or 0) - (cass_inventory_value or 0)
        gross_profit = (total_sales or 0) - (cass_cost_of_stock_sold or 0)

        # Debts (DB-side aggregate for supplier debt)
        from sqlalchemy import func
        supplier_debt = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).scalar()
        customer_debt = total_debt

        # Cash position indicator for cassiterite
        cash_position = gross_profit - customer_debt + supplier_debt

        user_notifications = []
        unread = []
        read = []
        unread_count = 0
        if getattr(current_user, "is_authenticated", False):
            # Show all unread notifications and up to 10 already-read notifications
            # Avoid joining the `user` table here; fall back to an empty list
            # and rollback on error so a permissions problem doesn't abort
            # the whole request.
            try:
                user_notifications, unread_count = fetch_user_notifications(getattr(current_user, 'id', None), unread_limit=20, read_limit=10)
            except Exception:
                logger.exception("cassiterite.dashboard: fetch_user_notifications helper failed")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                user_notifications = []
                unread_count = 0

        # Cassiterite moyenne is stored on each stock; compute global moyenne like copper
        # Avoid materializing all remaining stocks in memory — just compute the count.
        remaining_stocks_count = CassiteriteStock.query.filter(CassiteriteStock.local_balance > 0).count()
        total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
        total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
        moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0

        logger.info("cassiterite.dashboard: completed page=%s stocks_shown=%d", page, len(stocks))
        return render_template(
            'cassiterite/dashboard.html',
            stocks=stocks,
            voucher_choices=voucher_choices,
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
            unread_notifications_count=unread_count,
            moyenne=moyenne,
            remaining_stocks_count=remaining_stocks_count,
            stocks_pagination=stocks_pagination,
            page=page,
            per_page=per_page,
        )
    except Exception:
        logger.exception("cassiterite.dashboard failed page=%s", request.args.get('page'))
        raise


@role_required("accountant")
@cassiterite_bp.route('/api/filter_stocks', methods=['POST'])
@trace_time
def cassiterite_filter_stocks():
    """Filter cassiterite stocks by date range (and optional lot/voucher) and return JSON with metrics and outputs."""
    
    try:
        from cassiterite.models import CassiteriteOutput
        logger.info("cassiterite.filter_stocks: start params=%s", request.get_json() or {})
            # Reset session to avoid reusing an aborted connection from the pool
        try:
                db.session.remove()
        except Exception:
                
                try:
                    db.session.rollback()
                except Exception:
                    pass
        data = request.get_json() or {}
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        lot_no = data.get('lot_no') or None

    # Base queries
        stocks_query = CassiteriteStock.query.order_by(CassiteriteStock.date.desc())
        outputs_query = CassiteriteOutput.query.order_by(CassiteriteOutput.date.desc())

        from datetime import datetime as _dt

        if start_date:
            start = _dt.strptime(start_date, '%Y-%m-%d').date()
            stocks_query = stocks_query.filter(CassiteriteStock.date >= start)
            outputs_query = outputs_query.filter(CassiteriteOutput.date >= start)

        if end_date:
            end = _dt.strptime(end_date, '%Y-%m-%d').date()
            stocks_query = stocks_query.filter(CassiteriteStock.date <= end)
            outputs_query = outputs_query.filter(CassiteriteOutput.date <= end)

        if lot_no:
            stocks_query = stocks_query.filter(CassiteriteStock.voucher_no == lot_no)

        # Build page using a single SQL round-trip returning paged stocks with
        # per-stock outputs_sum and running cumulative totals to avoid ORM
        # pagination + Python-level per-row aggregation (reduces DB RTTs).
        page = int(data.get('page', 1) or 1)
        per_page = int(data.get('per_page', 20) or 20)
        if per_page < 5:
            per_page = 5
        if per_page > 100:
            per_page = 100
        include_all = bool(data.get('include_all'))

        offset = (page - 1) * per_page

        # Build SQL WHERE fragments for stocks and outputs based on filters
        stock_where = '1=1'
        output_where = '1=1'
        params = {'per_page': per_page, 'offset': offset}
        if start_date:
            stock_where += ' AND s.date >= :start'
            output_where += ' AND o.date >= :start'
            params['start'] = start
        if end_date:
            stock_where += ' AND s.date <= :end'
            output_where += ' AND o.date <= :end'
            params['end'] = end
        if lot_no:
            stock_where += ' AND s.voucher_no = :lot_no'
            params['lot_no'] = lot_no

        from sqlalchemy import text
        try:
            page_sql = f"""
WITH outputs_sum AS (
  SELECT stock_id, COALESCE(SUM(output_kg),0) AS outputs_sum
  FROM cassiterite_output o
  WHERE {output_where}
  GROUP BY stock_id
), ordered AS (
  SELECT
    s.id,
    s.date,
    s.voucher_no,
    s.supplier,
    s.input_kg,
    s.percentage,
    s.unit_percent,
    s.t_unity,
    s.moyenne,
    s.lme,
    s.m_lme,
    s.exchange,
    s.sec,
    s.tc,
    s.u_price,
    s.amount,
    s.amount_with_taxes,
    s.transport_tag,
    s.tot_amount_tag,
    s.rma,
    s.inkomane,
    s.rra_3_percent,
    s.local_balance,
    s.balance_to_pay,
    s.total_balance,
    s.net_balance,
    COALESCE(os.outputs_sum,0) AS outputs_sum,
    COALESCE(SUM(s.net_balance) OVER (ORDER BY s.date, s.id),0) AS cumulative
  FROM cassiterite_stock s
  LEFT JOIN outputs_sum os ON os.stock_id = s.id
  WHERE s.local_balance > 0 AND {stock_where}
)
SELECT sub.*, (SELECT COALESCE(COUNT(1),0) FROM cassiterite_stock s WHERE s.local_balance > 0 AND {stock_where}) AS total_count
FROM (
  SELECT * FROM ordered
  ORDER BY date DESC, id DESC
  LIMIT :per_page OFFSET :offset
) sub
"""

            rows = db.session.execute(text(page_sql), params).mappings().all()
            total_count = int(rows[0]['total_count']) if rows else 0
            page_stock_ids = [int(r['id']) for r in rows]
            outputs_sums = {int(r['id']): float(r['outputs_sum'] or 0) for r in rows}
            # fetch filtered_outputs for charting (small result set)
            if page_stock_ids:
                outputs_q = db.session.query(CassiteriteOutput.date, CassiteriteOutput.output_kg).filter(CassiteriteOutput.stock_id.in_(page_stock_ids))
                if start_date:
                    outputs_q = outputs_q.filter(CassiteriteOutput.date >= start)
                if end_date:
                    outputs_q = outputs_q.filter(CassiteriteOutput.date <= end)
                outputs_q = outputs_q.order_by(CassiteriteOutput.date.desc())
                filtered_outputs = outputs_q.all()
            else:
                filtered_outputs = []
            # provide a small stocks_pagination fallback object for the payload
            from types import SimpleNamespace
            pages = (total_count + per_page - 1) // per_page if total_count else 1
            stocks_pagination = SimpleNamespace(pages=pages, total=total_count)
            # Build lightweight objects for serialization so the rest of the
            # handler can treat `filtered_stocks` like ORM objects.
            filtered_stocks = [SimpleNamespace(**r) for r in rows] if rows else []
            timings = locals().get('timings', {})
            timings['page_sql'] = 0.0
        except Exception:
            logger.exception('cassiterite.filter_stocks: page SQL failed; falling back to ORM paginate')
            stocks_local_q = stocks_query.filter(CassiteriteStock.local_balance > 0)
            stocks_pagination = stocks_local_q.paginate(page=page, per_page=per_page, error_out=False)
            filtered_stocks = stocks_pagination.items
            page_stock_ids = [s.id for s in filtered_stocks]
            filtered_outputs = outputs_query.filter(CassiteriteOutput.stock_id.in_(page_stock_ids)).all() if page_stock_ids else []
            from collections import defaultdict as _dd
            outputs_sums = _dd(float)
            if page_stock_ids:
                try:
                    rows = db.session.query(CassiteriteOutput.stock_id, func.coalesce(func.sum(CassiteriteOutput.output_kg), 0)).filter(CassiteriteOutput.stock_id.in_(page_stock_ids))
                    if start_date:
                        rows = rows.filter(CassiteriteOutput.date >= start)
                    if end_date:
                        rows = rows.filter(CassiteriteOutput.date <= end)
                    rows = rows.group_by(CassiteriteOutput.stock_id).all()
                    for sid, ssum in rows:
                        outputs_sums[sid] = float(ssum or 0)
                except Exception:
                    for o in filtered_outputs:
                        if o and o.stock_id:
                            outputs_sums[o.stock_id] += float(o.output_kg or 0)

        # Build common stock filters for DB-side aggregates
        stock_filters = []
        if start_date:
                
                stock_filters.append(CassiteriteStock.date >= start)
        if end_date:
                stock_filters.append(CassiteriteStock.date <= end)
        if lot_no:
                stock_filters.append(CassiteriteStock.voucher_no == lot_no)

            # Aggregates from DB (faster and avoids loading full tables into Python).
            # stock_filters here represent the original cost-basis window (lots
            # purchased from suppliers in this filtered period).
        total_input = db.session.query(func.coalesce(func.sum(CassiteriteStock.input_kg), 0)).filter(*stock_filters).scalar() or 0
        total_stocks = db.session.query(func.coalesce(func.count(CassiteriteStock.id), 0)).filter(*stock_filters).scalar() or 0

        output_filters = []
        if start_date:
            output_filters.append(CassiteriteOutput.date >= start)
        if end_date:
            output_filters.append(CassiteriteOutput.date <= end)

        total_output = db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_kg), 0)).filter(*output_filters).scalar() or 0

        # Customer outstanding debt from single source of truth: plans - receipts
        try:
            plan_q = (
                db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                .filter(
                    BulkOutputPlan.mineral_type.in_(['cassiterite']),
                    BulkOutputPlan.total_expected_amount.isnot(None),
                    BulkOutputPlan.total_expected_amount > 0,
                    BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                )
            )
            receipts_q = (
                db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
                .filter(CustomerReceipt.mineral_type.in_(['cassiterite']))
            )
            if start_date:
                plan_q = plan_q.filter(BulkOutputPlan.created_at >= _dt.combine(start, _dt.min.time()))
                receipts_q = receipts_q.filter(CustomerReceipt.received_at >= _dt.combine(start, _dt.min.time()))
            if end_date:
                plan_q = plan_q.filter(BulkOutputPlan.created_at <= _dt.combine(end, _dt.max.time()))
                receipts_q = receipts_q.filter(CustomerReceipt.received_at <= _dt.combine(end, _dt.max.time()))
            expected_amt = plan_q.scalar() or 0.0
            paid_amt = receipts_q.scalar() or 0.0
            total_debt = float(expected_amt or 0.0) - float(paid_amt or 0.0)
        except Exception:
            logger.exception("cassiterite.filter_stocks: failed computing customer debt from plans/receipts")
            total_debt = 0.0

        # Total sales (monetary) must follow the same source of truth as debt.
        # We therefore sum BulkOutputPlan.total_expected_amount for the same
        # mineral type and date window.
        try:
            sales_q = (
                db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                .filter(
                    BulkOutputPlan.mineral_type.in_(['cassiterite']),
                    BulkOutputPlan.total_expected_amount.isnot(None),
                    BulkOutputPlan.total_expected_amount > 0,
                    BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                )
            )
            if start_date:
                sales_q = sales_q.filter(BulkOutputPlan.created_at >= _dt.combine(start, _dt.min.time()))
            if end_date:
                sales_q = sales_q.filter(BulkOutputPlan.created_at <= _dt.combine(end, _dt.max.time()))
            total_sales = float(sales_q.scalar() or 0.0)
        except Exception:
            logger.exception('cassiterite.filter_stocks: total_sales aggregate from plans failed')
            total_sales = 0.0

        # Total supplier obligation (balance_to_pay) respecting the same stock filters
        total_supplier_obligation = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).filter(*stock_filters).scalar() or 0

        # Total payments made against the filtered cassiterite stocks
        from cassiterite.models import CassiteriteSupplierPayment
        total_payments = db.session.query(func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0)).join(CassiteriteStock, CassiteriteSupplierPayment.stock_id == CassiteriteStock.id).filter(*stock_filters).scalar() or 0

        # Inventory value (book cost) and supplier outstanding (liability)
        inventory_value = total_supplier_obligation
        supplier_outstanding = (inventory_value or 0) - (total_payments or 0)

        # Gross profit for the filtered window
        gross_profit = (total_sales or 0) - (total_supplier_obligation or 0)

        # Remaining stocks aggregates (only local_balance > 0)
        remaining_filters = list(stock_filters) + [CassiteriteStock.local_balance > 0]
        # If no date/lot filters are provided, prefer the lightweight
        # single-row StockAggregate to avoid expensive SUMs.
        total_unit_percent = 0
        total_remaining_balance = 0
        moyenne = 0
        if not (start_date or end_date or lot_no):
            try:
                from core.models import StockAggregate
                agg = StockAggregate.get('cassiterite')
                if agg:
                    total_unit_percent = float(agg.total_weighted_percent or 0.0)
                    total_remaining_balance = float(agg.total_quantity or 0.0)
                    moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                else:
                    raise RuntimeError('no aggregate')
            except Exception:
                total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
                total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
        else:
            total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
            total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
            moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
            # Total supplier obligation (balance_to_pay) respecting the same stock
            # filters. This is the original cost basis for these lots (what we
            # owe suppliers before any payments are deducted).
           
        total_supplier_obligation = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).filter(*stock_filters).scalar() or 0

            # Total payments made against the filtered cassiterite stocks
        from cassiterite.models import CassiteriteSupplierPayment
        total_payments = db.session.query(func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0)).join(CassiteriteStock, CassiteriteSupplierPayment.stock_id == CassiteriteStock.id).filter(*stock_filters).scalar() or 0

            # Remaining stocks aggregates (only local_balance > 0). We will reuse
            # this both for moyenne and Inventory Value (current cost of remaining
            # stock).
        remaining_filters = list(stock_filters) + [CassiteriteStock.local_balance > 0]

            # Inventory Value (current cost of remaining cassiterite stock).
            # Per lot: cost_per_kg = balance_to_pay / input_kg, then
            # current_value = cost_per_kg * local_balance.
            # Implemented in SQL as SUM(balance_to_pay * local_balance / input_kg)
            # and restricted to positive input_kg to avoid division-by-zero.
        remaining_value_filters = list(remaining_filters) + [CassiteriteStock.input_kg > 0]
        import time
        timings = {}
        try:
            t_inv = time.perf_counter()
            inventory_value = db.session.query(
                func.coalesce(
                    func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / CassiteriteStock.input_kg),
                    0,
                )
            ).filter(*remaining_value_filters).scalar() or 0
            timings['inventory_value'] = time.perf_counter() - t_inv
        except Exception:
            logger.exception('cassiterite.filter_stocks: inventory_value aggregate failed')
            inventory_value = 0
            timings['inventory_value'] = None

            # Supplier outstanding (liability) is based on the original supplier
            # obligation minus all payments recorded for these lots.
        supplier_outstanding = (total_supplier_obligation or 0) - (total_payments or 0)

            # Gross profit for the filtered window remains tied to the original
            # cost basis, not the current Inventory Value.
        gross_profit = (total_sales or 0) - (total_supplier_obligation or 0)
            # Remaining stocks aggregates (only local_balance > 0)
        total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0

        # Serialize stocks
        # Build a per-stock outputs sum using DB GROUP BY (avoid Python loops)
        from collections import defaultdict
        outputs_sums = defaultdict(float)
        if page_stock_ids:
            try:
                rows = db.session.query(CassiteriteOutput.stock_id, func.coalesce(func.sum(CassiteriteOutput.output_kg), 0)).filter(CassiteriteOutput.stock_id.in_(page_stock_ids))
                if start_date:
                    rows = rows.filter(CassiteriteOutput.date >= start)
                if end_date:
                    rows = rows.filter(CassiteriteOutput.date <= end)
                rows = rows.group_by(CassiteriteOutput.stock_id).all()
                for sid, ssum in rows:
                    outputs_sums[sid] = float(ssum or 0)
            except Exception:
                for o in filtered_outputs:
                    if o and o.stock_id:
                        outputs_sums[o.stock_id] += float(o.output_kg or 0)

        stocks_data = []
        for s in filtered_stocks:
            stocks_data.append({
                'id': s.id,
                'date': s.date.strftime('%Y-%m-%d'),
                'voucher_no': s.voucher_no,
                'supplier': s.supplier,
                'input_kg': round(s.input_kg or 0, 2),
                'percentage': round(s.percentage or 0, 2),
                'unit_percent': round(s.unit_percent or 0, 4),
                't_unity': round(s.t_unity or 0, 2),
                'moyenne': round(s.moyenne or 0, 4),
                'lme': round(s.lme or 0, 2),
                'm_lme': round(s.m_lme or 0, 2),
                'exchange': round(s.exchange or 0, 2),
                'sec': round(s.sec or 0, 2),
                'tc': round(s.tc or 0, 2),
                'u_price': round(s.u_price or 0, 2),
                'amount': round(s.amount or 0, 2),
                'amount_with_taxes': round(s.amount_with_taxes or 0, 2),
                'transport_tag': round(s.transport_tag or 0, 2),
                'tot_amount_tag': round(s.tot_amount_tag or 0, 2),
                'rma': round(s.rma or 0, 2),
                'inkomane': round(s.inkomane or 0, 2),
                'rra_3_percent': round(s.rra_3_percent or 0, 2),
                'local_balance': round(s.local_balance or 0, 2),
                'balance_to_pay': round(s.balance_to_pay or 0, 2),
                'total_balance': round(s.total_balance or 0, 2),
                'remaining': round(((s.input_kg or 0) - outputs_sums.get(s.id, 0)) or 0, 2),
            })

        # Serialize outputs for chart
        outputs_data = []
        for o in filtered_outputs:
            outputs_data.append({
                'date': o.date.strftime('%Y-%m-%d'),
                'output_kg': round(o.output_kg or 0, 2)
            })

        # Compute period COGS from recorded outputs linked to lots. For each
        # output row cost = output_kg * (stock.balance_to_pay / NULLIF(stock.input_kg,0)).
        # This ensures COGS counts only goods actually sold in the window.
        try:
            t_cogs = time.perf_counter()
            cogs_q = db.session.query(
                func.coalesce(
                    func.sum(
                        CassiteriteOutput.output_kg * (CassiteriteStock.balance_to_pay / func.nullif(CassiteriteStock.input_kg, 0))
                    ),
                    0.0,
                )
            ).join(CassiteriteStock, CassiteriteOutput.stock_id == CassiteriteStock.id)

            if output_filters:
                for f in output_filters:
                    cogs_q = cogs_q.filter(f)

            cost_of_stock_sold = float(cogs_q.scalar() or 0.0)
            timings['cogs_aggregate'] = time.perf_counter() - t_cogs
        except Exception:
            logger.exception("cassiterite.filter_stocks: failed computing COGS from outputs; falling back")
            cost_of_stock_sold = (total_supplier_obligation or 0) - (inventory_value or 0)
            timings['cogs_aggregate'] = None
        logger.info("cassiterite.filter_stocks: completed stocks=%d outputs=%d page=%d", len(filtered_stocks), len(filtered_outputs), page)
        logger.info('cassiterite.filter_stocks timings: %s', timings)
        return jsonify({
            'stocks': stocks_data,
            'outputs': outputs_data,
            'page': page,
            'per_page': per_page,
            'pages': stocks_pagination.pages if 'stocks_pagination' in locals() else 1,
            'total': stocks_pagination.total if 'stocks_pagination' in locals() else len(stocks_data),
            'total_input': round(total_input, 2),
            'total_output': round(total_output, 2),
            'total_debt': round(total_debt, 2),
            'total_stocks': total_stocks,
            'moyenne': round(moyenne, 4),

            # Added fields for filtered financial view
            'total_sales': round(total_sales, 2),
            'total_supplier_obligation': round(total_supplier_obligation, 2),
            'inventory_value': round(inventory_value, 2),
            'cost_of_stock_sold': round(cost_of_stock_sold, 2),
            'total_payments': round(total_payments, 2),
            'supplier_outstanding': round(supplier_outstanding, 2),
            'gross_profit': round(gross_profit, 2),
        })
    except Exception:
        logger.exception("cassiterite.filter_stocks failed params=%s", request.get_json() or {})
        raise
