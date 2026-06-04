"""
Stock Routes
Handles copper stock entry and export functionality
"""
from datetime import datetime
from io import BytesIO
from flask import render_template, request, redirect, url_for, flash, send_file
import openpyxl
import pandas as pd

from config import db
from copper.models import CopperStock, CopperOutput, SupplierPayment, CopperAdvanceAllocation
from copper import copper_bp
from core.auth import role_required
from core.models import Notification, create_notification, User, fetch_user_notifications, BulkOutputPlan, BulkPlanStatus, CustomerReceipt, StockChangeLog
from sqlalchemy.orm import joinedload, selectinload
from flask_login import current_user
from sqlalchemy import func
import logging
from utils import trace_time, safe_jsonify
import time
from threading import Lock

logger = logging.getLogger(__name__)


def _stock_has_payment_history(stock_id: int) -> bool:
    """Return True if this supplier has any payment activity in the consolidated ledger."""
    from copper.models import SupplierPayment, CopperSupplier
    try:
        stock = CopperStock.query.get(stock_id)
        supplier_name = (getattr(stock, 'supplier', None) or '').strip() if stock else ''
        if not supplier_name:
            result = None
        else:
            normalized_supplier = supplier_name.lower()
            from sqlalchemy import func as _func, or_ as _or

            supplier_row = CopperSupplier.query.filter(CopperSupplier.name == supplier_name).first()
            supplier_id = getattr(supplier_row, 'id', None)

            copper_hit = db.session.query(SupplierPayment.id).filter(
                _or(
                    SupplierPayment.stock_id.in_(
                        db.session.query(CopperStock.id).filter(
                            CopperStock.is_deleted.is_(False),
                            _func.lower(_func.trim(CopperStock.supplier)) == normalized_supplier,
                        )
                    ),
                    _func.lower(_func.trim(SupplierPayment.supplier_name)) == normalized_supplier,
                    SupplierPayment.supplier_id == supplier_id if supplier_id else False,
                )
            ).first()

            cass_hit = None
            try:
                from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment, CassiteriteSupplier
                cass_supplier_row = CassiteriteSupplier.query.filter(CassiteriteSupplier.name == supplier_name).first()
                cass_supplier_id = getattr(cass_supplier_row, 'id', None)
                cass_hit = db.session.query(CassiteriteSupplierPayment.id).filter(
                    _or(
                        CassiteriteSupplierPayment.stock_id.in_(
                            db.session.query(CassiteriteStock.id).filter(
                                CassiteriteStock.is_deleted.is_(False),
                                _func.lower(_func.trim(CassiteriteStock.supplier)) == normalized_supplier,
                            )
                        ),
                        _func.lower(_func.trim(CassiteriteSupplierPayment.supplier_name)) == normalized_supplier,
                        CassiteriteSupplierPayment.supplier_id == cass_supplier_id if cass_supplier_id else False,
                    )
                ).first()
            except Exception:
                cass_hit = None

            unified_hit = None
            try:
                from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation
                unified_hit = (
                    db.session.query(UnifiedSupplierAdvance.id)
                    .filter(
                        UnifiedSupplierAdvance.is_deleted.is_(False),
                        UnifiedSupplierAdvance.supplier_name_norm == normalized_supplier,
                    )
                    .first()
                )
                if not unified_hit:
                    unified_hit = (
                        db.session.query(UnifiedSupplierAdvanceAllocation.id)
                        .join(UnifiedSupplierAdvance, UnifiedSupplierAdvance.id == UnifiedSupplierAdvanceAllocation.advance_id)
                        .filter(
                            UnifiedSupplierAdvance.is_deleted.is_(False),
                            UnifiedSupplierAdvance.supplier_name_norm == normalized_supplier,
                        )
                        .first()
                    )
            except Exception:
                unified_hit = None

            result = copper_hit or cass_hit or unified_hit
        has_payment = result is not None
        logger.debug("_stock_has_payment_history: stock_id=%s result=%s has_payment=%s", stock_id, result, has_payment)
        return has_payment
    except Exception as e:
        logger.exception("_stock_has_payment_history failed for stock_id=%s", stock_id)
        return False

# Simple cache for dashboard aggregates to avoid repeated heavy SQL
_AGG_CACHE = {}
_AGG_CACHE_LOCK = Lock()


def _get_dashboard_aggregates(ttl=1):
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
    """Soft-delete a copper stock and redirect to dashboard."""
    try:
        logger.info("delete_stock: start id=%s user=%s", stock_id, getattr(current_user, "username", None))
        stock = CopperStock.query.get_or_404(stock_id)
        voucher = stock.voucher_no
        
        # Check if stock has ever had supplier payments - if so, require boss approval
        has_payments = _stock_has_payment_history(stock_id)
        logger.info("delete_stock: stock_id=%s has_payments=%s (bool=%s)", stock_id, has_payments, bool(has_payments))
        
        if has_payments:
            # Create approval request instead of directly deleting
            from core.models import PaymentReview, PaymentReviewStatus
            import json

            existing = PaymentReview.query.filter_by(
                payment_id=stock_id,
                type='stock_delete',
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                flash(f"A delete request for stock {voucher} is already pending boss review.", "warning")
                return redirect(url_for("copper.dashboard"))
            
            payload = {
                'action': 'delete_stock',
                'stock_id': stock_id,
                'voucher_no': voucher,
                'supplier': stock.supplier,
                'delete_reason': request.form.get('delete_reason') or 'Deleted from dashboard.',
                'note': request.form.get('delete_reason') or 'Deleted from dashboard.',
                'mineral_type': 'copper'
            }
            
            review = PaymentReview(
                mineral_type='copper',
                type='stock_delete',
                customer=f"Stock {voucher} - {stock.supplier}",
                amount=float(stock.net_balance or 0),
                currency='RWF',
                payment_id=stock_id,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=json.dumps(payload),
                boss_comment=f"Request to delete stock {voucher} which has supplier payments. Reason: {request.form.get('delete_reason') or 'Deleted from dashboard.'}"
            )
            db.session.add(review)
            db.session.commit()
            
            # Notify all bosses
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_delete_approval",
                    message=f"Kontabure {getattr(current_user, 'username', 'unknown')} asabye kwemeza gusibwa kwa stock  {voucher} (hari abatanga ibicuruzwa bamaze kwishyurwa bivuzeko ayo tubarimwo arahinduka muri sisitemu).",
                    related_type="payment_review",
                    related_id=review.id
                )
            
            flash(f"Stock {voucher} already have some supplier payments . so you will wait for boss approval.", "warning")
            return redirect(url_for("copper.dashboard"))
        
        # No supplier payments - proceed with direct deletion
        try:
            before_snapshot = {
                'id': int(stock.id),
                'date': str(stock.date) if getattr(stock, 'date', None) else None,
                'voucher_no': stock.voucher_no,
                'supplier': stock.supplier,
                'input_kg': float(stock.input_kg or 0.0),
                'percentage': float(stock.percentage or 0.0),
                'nb': float(getattr(stock, 'nb', 0.0) or 0.0),
                'net_balance': float(getattr(stock, 'net_balance', 0.0) or 0.0),
                'local_balance': float(getattr(stock, 'local_balance', 0.0) or 0.0),
            }

            # Compute and remove this stock's contribution from the aggregate
            try:
                contrib_q, contrib_wp, contrib_t = CopperStock.contribution(stock)
            except Exception:
                contrib_q = contrib_wp = contrib_t = 0.0

            stock.is_deleted = True
            stock.deleted_at = datetime.utcnow()
            stock.deleted_by_id = getattr(current_user, 'id', None)
            stock.delete_reason = request.form.get('delete_reason') or 'Deleted from dashboard.'
            db.session.add(stock)

            try:
                log_row = StockChangeLog(
                    mineral_type='copper',
                    stock_id=int(stock.id),
                    action='DELETE',
                    reason=stock.delete_reason,
                    before_json=before_snapshot,
                    after_json={'is_deleted': True},
                    created_by_id=getattr(current_user, 'id', None),
                )
                db.session.add(log_row)
                db.session.flush()
            except Exception:
                logger.exception('delete_stock: failed to create StockChangeLog')
                log_row = None

            # Apply delta to the single-row aggregate (remove contribution)
            try:
                CopperStock.apply_aggregate_delta(-contrib_q, -contrib_wp, -contrib_t)
            except Exception:
                logger.exception("delete_stock: failed to apply aggregate delta after delete")
            # (cache invalidation moved to after commit so it only clears on success)

            # Notify all bosses (fetch ids only to avoid hydrating full User objects)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_delete",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} deleted copper stock {voucher}.",
                    related_type="stock_change_log" if log_row else "copper_stock",
                    related_id=(int(getattr(log_row, 'id', 0)) if log_row else stock_id)
                )

            db.session.commit()
            # Invalidate dashboard cache so clients don't read stale aggregates
            try:
                _set_dashboard_aggregates(None, ttl=0)
            except Exception:
                pass

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
        voucher = stock.voucher_no
        
        # Check if stock has ever had supplier payments - if so, require boss approval
        has_payments = _stock_has_payment_history(stock_id)
        logger.info("edit_stock: stock_id=%s has_payments=%s (bool=%s)", stock_id, has_payments, bool(has_payments))
        
        if has_payments:
            # Create approval request instead of directly editing
            from core.models import PaymentReview, PaymentReviewStatus
            import json

            existing = PaymentReview.query.filter_by(
                payment_id=stock_id,
                type='stock_edit',
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).first()
            if existing:
                flash(f"An edit request for stock {voucher} is already pending boss review.", "warning")
                return redirect(url_for("copper.dashboard"))
            
            # Parse incoming fields for the payload
            date = _parse_date(request.form.get("date")) or stock.date
            new_voucher = request.form.get("voucher_no") or stock.voucher_no
            supplier = request.form.get("supplier") or stock.supplier
            input_kg = float(request.form.get("input_kg") or stock.input_kg or 0)
            percentage = float(request.form.get("percentage") or stock.percentage or 0)
            nb = float(request.form.get("nb") or stock.nb or 0)
            u_price = float(request.form.get("u_price") or stock.u_price or 0)
            exchange = float(request.form.get("exchange") or stock.exchange or 0)
            transport_tag = float(request.form.get("transport_tag") or stock.transport_tag or 0)
            rma_default_str = request.form.get('rma_default') or str(stock.rma / stock.input_kg if (stock.input_kg and stock.rma) else 150)
            inkomane_default_str = request.form.get('inkomane_default') or str(stock.inkomane / stock.input_kg if (stock.input_kg and stock.inkomane) else 40)
            rra_3_percent_default_str = request.form.get('rra_3_percent_default') or '50'
            rma_default = float(rma_default_str) if rma_default_str else 150
            inkomane_default = float(inkomane_default_str) if inkomane_default_str else 40
            rra_3_percent_default = float(rra_3_percent_default_str) if rra_3_percent_default_str else 50
            change_reason = (request.form.get('change_reason') or '').strip() or None
            
            payload = {
                'action': 'edit_stock',
                'stock_id': stock_id,
                'voucher_no': voucher,
                'new_voucher_no': new_voucher,
                'supplier': supplier,
                'date': str(date) if date else None,
                'input_kg': input_kg,
                'percentage': percentage,
                'nb': nb,
                'u_price': u_price,
                'exchange': exchange,
                'transport_tag': transport_tag,
                'rma_default': rma_default,
                'inkomane_default': inkomane_default,
                'rra_3_percent_default': rra_3_percent_default,
                'change_reason': change_reason,
                'note': change_reason or 'No reason provided',
                'mineral_type': 'copper'
            }
            
            review = PaymentReview(
                mineral_type='copper',
                type='stock_edit',
                customer=f"Stock {voucher} - {stock.supplier}",
                amount=float(stock.net_balance or 0),
                currency='RWF',
                payment_id=stock_id,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=json.dumps(payload),
                boss_comment=f"Request to edit stock {voucher} which has supplier payments. Reason: {change_reason or 'No reason provided'}"
            )
            db.session.add(review)
            db.session.commit()
            
            # Notify all bosses
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_edit_approval",
                    message=f"Kontabure {getattr(current_user, 'username', 'unknown')} asabye kwemeza guhindura ingano {voucher} (hari abatanga ibicuruzwa bamaze kwishyurwa bivuzeko ayo tubarimwo arahinduka muri sisitemu).",
                    related_type="payment_review",
                    related_id=review.id
                )
            
            flash(f"Stcok  {voucher} already have some supplier payments , so it will require boss approval. ", "warning")
            return redirect(url_for("copper.dashboard"))

        # No supplier payments - proceed with direct edit
        before_snapshot = {
            'id': int(stock.id),
            'date': str(stock.date) if getattr(stock, 'date', None) else None,
            'voucher_no': stock.voucher_no,
            'supplier': stock.supplier,
            'input_kg': float(stock.input_kg or 0.0),
            'percentage': float(stock.percentage or 0.0),
            'nb': float(getattr(stock, 'nb', 0.0) or 0.0),
            'u_price': float(getattr(stock, 'u_price', 0.0) or 0.0),
            'exchange': float(getattr(stock, 'exchange', 0.0) or 0.0),
            'transport_tag': float(getattr(stock, 'transport_tag', 0.0) or 0.0),
            'rma': float(getattr(stock, 'rma', 0.0) or 0.0),
            'inkomane': float(getattr(stock, 'inkomane', 0.0) or 0.0),
            'rra_3': float(getattr(stock, 'rra_3', 0.0) or 0.0),
            'rra_3_percent': float(getattr(stock, 'rra_3_percent', 0.0) or 0.0),
        }

        change_reason = (request.form.get('change_reason') or '').strip() or None

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
        
        # Parse editable per-unit defaults
        rma_default_str = request.form.get("rma_default")
        inkomane_default_str = request.form.get("inkomane_default")
        rra_3_percent_default_str = request.form.get("rra_3_percent_default")
        
        # If defaults provided in form, use them; otherwise derive from current stock
        if rma_default_str and rma_default_str.strip():
            try:
                rma_default = float(rma_default_str)
            except ValueError:
                rma_default = (stock.rma / stock.input_kg) if (stock.input_kg and stock.rma) else 150
        else:
            rma_default = (stock.rma / stock.input_kg) if (stock.input_kg and stock.rma) else 150
        
        if inkomane_default_str and inkomane_default_str.strip():
            try:
                inkomane_default = float(inkomane_default_str)
            except ValueError:
                inkomane_default = (stock.inkomane / stock.input_kg) if (stock.input_kg and stock.inkomane) else 40
        else:
            inkomane_default = (stock.inkomane / stock.input_kg) if (stock.input_kg and stock.inkomane) else 40
        
        if rra_3_percent_default_str and rra_3_percent_default_str.strip():
            try:
                rra_3_percent_default = float(rra_3_percent_default_str)
            except ValueError:
                rra_3_percent_default = 50
        else:
            rra_3_percent_default = 50

        # Duplicate voucher check if changed
        if voucher != stock.voucher_no:
            existing = (
                CopperStock.query
                .filter(CopperStock.voucher_no == voucher, CopperStock.is_deleted.is_(False))
                .first()
            )
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
        
        # Recalculate using per-unit defaults (same formula as add_stock)
        stock.rma = rma_default * input_kg
        stock.inkomane = inkomane_default * input_kg
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

            log_row = None
            try:
                after_snapshot = {
                    'id': int(stock.id),
                    'date': str(stock.date) if getattr(stock, 'date', None) else None,
                    'voucher_no': stock.voucher_no,
                    'supplier': stock.supplier,
                    'input_kg': float(stock.input_kg or 0.0),
                    'percentage': float(stock.percentage or 0.0),
                    'nb': float(getattr(stock, 'nb', 0.0) or 0.0),
                    'u_price': float(getattr(stock, 'u_price', 0.0) or 0.0),
                    'exchange': float(getattr(stock, 'exchange', 0.0) or 0.0),
                    'transport_tag': float(getattr(stock, 'transport_tag', 0.0) or 0.0),
                    'rma': float(getattr(stock, 'rma', 0.0) or 0.0),
                    'inkomane': float(getattr(stock, 'inkomane', 0.0) or 0.0),
                    'rra_3': float(getattr(stock, 'rra_3', 0.0) or 0.0),
                    'rra_3_percent': float(getattr(stock, 'rra_3_percent', 0.0) or 0.0),
                }
                log_row = StockChangeLog(
                    mineral_type='copper',
                    stock_id=int(stock.id),
                    action='EDIT',
                    reason=change_reason,
                    before_json=before_snapshot,
                    after_json=after_snapshot,
                    created_by_id=getattr(current_user, 'id', None),
                )
                db.session.add(log_row)
                db.session.flush()
            except Exception:
                logger.exception('edit_stock: failed to create StockChangeLog')
                log_row = None

            # Notify all bosses (fetch ids only to avoid hydrating full User objects)
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="stock_edit",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} edited copper stock {voucher}.",
                    related_type="stock_change_log" if log_row else "copper_stock",
                    related_id=(int(getattr(log_row, 'id', 0)) if log_row else stock_id)
                )

            db.session.commit()
            # Invalidate dashboard cache so clients see updated aggregates immediately
            try:
                _set_dashboard_aggregates(None, ttl=0)
            except Exception:
                pass

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
    from utils import close_name_matches, normalize_counterparty_name

    form = CopperStockForm()
    advance_choices = []
    from core.models import UnifiedSupplierAdvance
    advance_rows = (
        db.session.query(
            UnifiedSupplierAdvance.id,
            UnifiedSupplierAdvance.supplier_name,
            UnifiedSupplierAdvance.advance_remaining,
            UnifiedSupplierAdvance.paid_at,
        )
        .filter(
            UnifiedSupplierAdvance.is_deleted.is_(False),
            UnifiedSupplierAdvance.advance_remaining > 0,
        )
        .order_by(UnifiedSupplierAdvance.paid_at.desc(), UnifiedSupplierAdvance.id.desc())
        .all()
    )
    for row in advance_rows:
        label = f"{row.supplier_name or 'Unknown supplier'} - Advance remaining: {float(row.advance_remaining or 0):,.2f} RWF"
        advance_choices.append((int(row.id), label))
    form.advance_payment_ids.choices = advance_choices
    if request.method == "POST":
        try:
            logger.info("add_stock: start user=%s", getattr(current_user, 'username', None))

            date = _parse_date(request.form.get("date")) or datetime.utcnow().date()
            voucher = request.form.get("voucher_no")
            supplier = request.form.get("supplier")
            supplier_norm = normalize_counterparty_name(supplier)
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
            requested_advance_ids = [int(v) for v in request.form.getlist('advance_payment_ids') if str(v).strip().isdigit()]

            # For production we maintain `total_balance` at read-time using a
            # windowed query. Avoid doing a full SUM(...) here — store the
            # per-row net_balance and compute cumulative totals when the
            # frontend requests paged results.
            total_balance = net_balance

            # Check for duplicate voucher
            existing = (
                CopperStock.query
                .filter(CopperStock.voucher_no == voucher, CopperStock.is_deleted.is_(False))
                .first()
            )
            if existing:
                return safe_jsonify({"error": f"Voucher number {voucher} already exists."}), 400

            confirm_new_supplier = (request.form.get('confirm_new_supplier') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
            try:
                from copper.models import CopperSupplier
                from cassiterite.models import CassiteriteSupplier
                existing_names = [r[0] for r in db.session.query(CopperSupplier.name).filter(CopperSupplier.is_deleted.is_(False)).all()]
                existing_names += [r[0] for r in db.session.query(CassiteriteSupplier.name).filter(CassiteriteSupplier.is_deleted.is_(False)).all()]
            except Exception:
                existing_names = []

            if supplier_norm:
                exact_exists = any(normalize_counterparty_name(n) == supplier_norm for n in existing_names)
                if not exact_exists:
                    close = close_name_matches(supplier, existing_names, limit=5, cutoff=0.86)
                    if close and not confirm_new_supplier:
                        flash(f"Supplier name looks similar to existing supplier(s): {', '.join(close[:3])}. Select the existing supplier or confirm you want to create a new one.", "warning")
                        return render_template("copper/add_stock.html", form=form)

            # Ensure supplier exists in master tables so it becomes selectable everywhere.
            clean_supplier = (supplier or '').strip()
            if clean_supplier:
                try:
                    from sqlalchemy import func
                    from copper.models import CopperSupplier
                    from cassiterite.models import CassiteriteSupplier

                    exists_copper = (
                        CopperSupplier.query
                        .filter(CopperSupplier.is_deleted.is_(False), func.lower(func.trim(CopperSupplier.name)) == clean_supplier.lower())
                        .first()
                    )
                    if not exists_copper:
                        db.session.add(CopperSupplier(name=clean_supplier))
                        db.session.flush()

                    exists_cass = (
                        CassiteriteSupplier.query
                        .filter(CassiteriteSupplier.is_deleted.is_(False), func.lower(func.trim(CassiteriteSupplier.name)) == clean_supplier.lower())
                        .first()
                    )
                    if not exists_cass:
                        db.session.add(CassiteriteSupplier(name=clean_supplier))
                        db.session.flush()
                except Exception:
                    pass

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
                total_balance=total_balance,
            )

            try:
                db.session.add(s)
                db.session.flush()

                if requested_advance_ids:
                    from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation

                    def _norm(nm):
                        return ' '.join((nm or '').strip().lower().split())

                    advance_rows = (
                        UnifiedSupplierAdvance.query
                        .filter(
                            UnifiedSupplierAdvance.id.in_(requested_advance_ids),
                            UnifiedSupplierAdvance.is_deleted.is_(False),
                            UnifiedSupplierAdvance.advance_remaining > 0,
                        )
                        .order_by(UnifiedSupplierAdvance.paid_at.asc(), UnifiedSupplierAdvance.id.asc())
                        .with_for_update()
                        .all()
                    )
                    if len(advance_rows) != len(set(requested_advance_ids)):
                        flash("One or more selected advances are no longer available.", "danger")
                        db.session.rollback()
                        return render_template("copper/add_stock.html", form=form)

                    total_allocated = 0.0
                    for advance_payment in advance_rows:
                        if _norm(advance_payment.supplier_name) != _norm(supplier):
                            flash("Selected advances must belong to the same supplier as the stock.", "danger")
                            db.session.rollback()
                            return render_template("copper/add_stock.html", form=form)

                        if total_allocated >= float(net_balance or 0.0):
                            break

                        advance_available = float(advance_payment.advance_remaining or 0.0)
                        if advance_available <= 0:
                            continue

                        apply_amount = min(float(net_balance or 0.0) - total_allocated, advance_available)
                        if apply_amount <= 0:
                            continue

                        total_allocated += apply_amount
                        advance_payment.advance_remaining = max(advance_available - apply_amount, 0.0)
                        if (advance_payment.source_mineral_type or '').strip().lower() not in {'copper', 'coltan'}:
                            db.session.add(UnifiedSupplierAdvanceAllocation(
                                advance_id=advance_payment.id,
                                stock_mineral_type='copper',
                                stock_id=int(s.id),
                                applied_amount=float(apply_amount),
                            ))

                        if (advance_payment.source_mineral_type or '').strip().lower() in {'copper', 'coltan'} and advance_payment.source_payment_id:
                            try:
                                src = SupplierPayment.query.get(int(advance_payment.source_payment_id))
                                if src and src.is_advance and not src.is_deleted:
                                    src.advance_remaining = float(advance_payment.advance_remaining or 0.0)
                                    db.session.add(src)
                                    db.session.add(CopperAdvanceAllocation(
                                        stock_id=s.id,
                                        supplier_payment_id=src.id,
                                        applied_amount=float(apply_amount),
                                    ))
                            except Exception:
                                pass

                    if total_allocated <= 0 and requested_advance_ids:
                        flash("Selected advances could not be applied to this stock.", "danger")
                        db.session.rollback()
                        return render_template("copper/add_stock.html", form=form)

                s.update_calculations()

                # Apply delta: add this stock's contribution to the single-row aggregate
                try:
                    q, wp, t = CopperStock.contribution(s)
                    CopperStock.apply_aggregate_delta(q, wp, t)
                except Exception:
                    logger.exception("add_stock: failed to apply aggregate delta")

                db.session.commit()
                db.session.commit()
                # Invalidate dashboard cache so clients see updated aggregates immediately
                try:
                    _set_dashboard_aggregates(None, ttl=0)
                except Exception:
                    pass

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
@trace_time
def dashboard():
    """Copper dashboard"""
    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 20
    # Avoid pre-loading related supplier_payments here to reduce hydration cost on dashboard.
    stocks_pagination = CopperStock.query.filter(CopperStock.is_deleted.is_(False), CopperStock.local_balance > 0).order_by(CopperStock.date.desc()).paginate(page=page, per_page=per_page, error_out=False)
    stocks = stocks_pagination.items
    outputs = CopperOutput.query.filter(CopperOutput.is_deleted.is_(False)).order_by(CopperOutput.date.desc()).limit(10).all()
    # Compute a small distinct list of voucher choices separately so the
    # template doesn't materialize voucher values by iterating over `stocks`.
    try:
        voucher_q = db.session.query(CopperStock.voucher_no).filter(CopperStock.is_deleted.is_(False), CopperStock.local_balance > 0).distinct().order_by(CopperStock.date.desc()).limit(200)
        voucher_choices = [v for (v,) in voucher_q.all() if v]
    except Exception:
        try:
            voucher_choices = [s.voucher_no for s in stocks if getattr(s, 'voucher_no', None)]
        except Exception:
            voucher_choices = []
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
    latest_achieved_moyenne = None
    latest_achieved_moyenne_nb = None

    try:
        latest_plan = (
            BulkOutputPlan.query
            .filter(
                BulkOutputPlan.mineral_type == 'coltan',
                BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
            )
            .order_by(BulkOutputPlan.created_at.desc())
            .first()
        )
        if latest_plan and latest_plan.plan_json:
            first_row = latest_plan.plan_json[0] if isinstance(latest_plan.plan_json, list) and latest_plan.plan_json else {}
            if isinstance(first_row, dict):
                achieved_moyenne_value = first_row.get('achieved_moyenne')
                achieved_moyenne_nb_value = first_row.get('achieved_moyenne_nb')
                if achieved_moyenne_value is not None:
                    latest_achieved_moyenne = float(achieved_moyenne_value)
                if achieved_moyenne_nb_value is not None:
                    latest_achieved_moyenne_nb = float(achieved_moyenne_nb_value)
    except Exception:
        latest_achieved_moyenne = None
        latest_achieved_moyenne_nb = None

    # Compute aggregates in real-time (no caching)
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

    # Calculate total input (all undeleted stock)
    try:
        total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).filter(CopperStock.is_deleted.is_(False)).scalar() or 0
    except Exception:
        logger.exception("dashboard: failed to compute total_input")
        try:
            db.session.rollback()
        except Exception:
            pass
        total_input = 0

    user_notifications = []
    unread_count = 0
    if getattr(current_user, "is_authenticated", False):
        # Show all unread notifications and up to 10 already-read notifications
        # Avoid joining the `user` table here — a missing permission on the
        # `user` table previously caused the whole request transaction to be
        # aborted. If notification fetching fails, rollback and continue
        # with an empty notifications list so the dashboard still renders.
        try:
            user_notifications, unread_count = fetch_user_notifications(getattr(current_user, 'id', None), unread_limit=20, read_limit=10)
        except Exception:
            logger.exception("dashboard: fetch_user_notifications helper failed")
            try:
                db.session.rollback()
            except Exception:
                logger.exception("dashboard: rollback after fetch_user_notifications failure also failed")
            user_notifications = []
            unread_count = 0

        # Remaining stocks aggregates (compute counts/aggregates only — avoid hydrating full lists)
        # Prefer the single-row StockAggregate for moyenne values to avoid SUM(...) on render.
        remaining_stocks_count = CopperStock.query.filter(CopperStock.is_deleted.is_(False), CopperStock.local_balance > 0).count()
        # Try StockAggregate first for the moyenne values (single-row, cheap)
        try:
            from core.models import StockAggregate
            agg = StockAggregate.get('copper')
            if agg and agg.total_quantity:
                total_unit_percent = float(agg.total_weighted_percent or 0)
                total_remaining_balance = float(agg.total_quantity or 0)
                total_t_unity = float(agg.total_t_unity or 0)
                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
            else:
                total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(
                    CopperStock.local_balance > 0,
                    CopperStock.is_deleted.is_(False),
                ).scalar() or 0
                total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(
                    CopperStock.local_balance > 0,
                    CopperStock.is_deleted.is_(False),
                ).scalar() or 0
                total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(
                    CopperStock.local_balance > 0,
                    CopperStock.is_deleted.is_(False),
                ).scalar() or 0
                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
        except Exception:
            total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            ).scalar() or 0
            total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            ).scalar() or 0
            total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            ).scalar() or 0
            moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
            moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0

        if latest_achieved_moyenne is not None:
            moyenne = latest_achieved_moyenne
        if latest_achieved_moyenne_nb is not None:
            moyenne_nb = latest_achieved_moyenne_nb

        # Ensure server-rendered rows display the global moyenne values (don't rely
        # on per-row stored fields which we may avoid updating on every insert).
        try:
            for s in stocks:
                s.moyenne = moyenne
                s.moyenne_nb = moyenne_nb
        except Exception:
            pass

    # Customer outstanding must match the customer ledger (plans - receipts).
    # This keeps the dashboard KPI consistent even when output rows are not
    # maintaining a debt_remaining field.
    try:
        total_expected_amount = (
            db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
            .filter(
                BulkOutputPlan.mineral_type.in_(['copper', 'coltan']),
                BulkOutputPlan.total_expected_amount.isnot(None),
                BulkOutputPlan.total_expected_amount > 0,
                BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
            )
            .scalar()
            or 0.0
        )
        total_paid_amount = (
            db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
            .filter(CustomerReceipt.mineral_type.in_(['copper', 'coltan']))
            .scalar()
            or 0.0
        )
        total_debt = float(total_expected_amount or 0.0) - float(total_paid_amount or 0.0)
        customer_debt = total_debt
        cash_position = float(total_sales or 0.0) - float(customer_debt or 0.0)
    except Exception:
        logger.exception("dashboard: failed computing customer debt from plans/receipts")
        try:
            db.session.rollback()
        except Exception:
            pass

    return render_template(
        'copper/dashboard.html',
        stocks=stocks,
        voucher_choices=voucher_choices,
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
        unread_notifications_count=unread_count,
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
    stocks = CopperStock.query.filter(CopperStock.is_deleted.is_(False)).order_by(CopperStock.date.desc()).all()
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
    # Compute global moyenne values from the lightweight aggregate (cheap)
    try:
        from core.models import StockAggregate
        agg = StockAggregate.get('copper')
        if agg and agg.total_quantity:
            moyenne = float(agg.total_weighted_percent or 0.0) / float(agg.total_quantity or 1.0)
            moyenne_nb = float(agg.total_t_unity or 0.0) / float(agg.total_quantity or 1.0)
        else:
            moyenne = 0.0
            moyenne_nb = 0.0
    except Exception:
        moyenne = 0.0
        moyenne_nb = 0.0

    # Build SQL and parameters for filtered export and let pandas run it
    from sqlalchemy import text
    sql = "SELECT date, voucher_no, supplier, input_kg, u_price, rma, inkomane, amount, percentage, nb, local_balance FROM copper_stock WHERE local_balance > 0 AND is_deleted IS FALSE ORDER BY date DESC"
    params = {}

    # Use pandas/sqlalchemy to read directly into a DataFrame (avoids Python loops)
    try:
        df = pd.read_sql_query(text(sql), con=db.session.bind, params=params)
    except Exception:
        # Fallback to ORM-based fetch if read_sql_query fails
        filtered_stocks = CopperStock.query.filter(CopperStock.is_deleted.is_(False), CopperStock.local_balance > 0).all()
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
    from flask import request
    from datetime import datetime
    try:
        data = request.get_json()
        logger.info("filter_stocks: start user=%s data=%s", getattr(current_user, 'username', None), data)
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        voucher_no = data.get('voucher_no') or None
        search_term = (data.get('search') or '').strip()
    
    # Filter stocks
        stocks_query = CopperStock.query.filter(CopperStock.is_deleted.is_(False)).order_by(CopperStock.date.desc())
        if search_term:
            search_like = f"%{search_term.lower()}%"
            stocks_query = stocks_query.filter(
                db.func.lower(CopperStock.voucher_no).ilike(search_like) | db.func.lower(CopperStock.supplier).ilike(search_like)
            )
        
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
        include_aggregates = True

        # Build page using a single SQL round-trip that returns the paged
        # stocks along with per-stock output sums and the cumulative
        # `total_balance` computed via a window function. This avoids
        # ORM pagination + Python-level per-row aggregation and eliminates
        # N+1 patterns for outputs on the page.
        from collections import defaultdict
        import time
        timings = {}

        # Pagination params
        offset = (page - 1) * per_page
        include_all = bool(data.get('include_all'))

        # Build SQL WHERE fragments for stocks and outputs based on optional filters
        stock_where = 's.is_deleted IS FALSE'
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
        if voucher_no:
            stock_where += ' AND s.voucher_no = :voucher_no'
            params['voucher_no'] = voucher_no
        if search_term:
            stock_where += ' AND (LOWER(s.voucher_no) LIKE :search_like OR LOWER(s.supplier) LIKE :search_like)'
            params['search_like'] = f"%{search_term.lower()}%"

        # Compose a single SQL that (1) computes per-stock outputs_sum, (2)
        # computes cumulative total_balance using a window over the filtered
        # stocks, and (3) returns the requested page. Also returns an overall
        # total_count via a subquery so the client can paginate.
        from sqlalchemy import text
        try:
            page_sql = f"""
WITH outputs_sum AS (
  SELECT stock_id, COALESCE(SUM(output_kg),0) AS outputs_sum
    FROM copper_output o
    JOIN copper_stock s ON o.stock_id = s.id
        WHERE s.is_deleted IS FALSE AND {output_where}
  GROUP BY stock_id
), ordered AS (
  SELECT
    s.id,
    s.date,
    s.voucher_no,
    s.supplier,
    s.input_kg,
    s.percentage,
    s.nb,
    s.u_price,
    s.amount,
    s.exchange,
    s.transport_tag,
    s.rma,
    s.inkomane,
    s.local_balance,
    s.unit_percent,
    s.t_unity,
    s.rra_3_percent,
    s.net_balance,
    COALESCE(os.outputs_sum,0) AS outputs_sum,
    COALESCE(SUM(s.net_balance) OVER (ORDER BY s.date, s.id),0) AS cumulative
  FROM copper_stock s
  LEFT JOIN outputs_sum os ON os.stock_id = s.id
  WHERE s.local_balance > 0 AND {stock_where}
)
SELECT sub.*, (SELECT COALESCE(COUNT(1),0) FROM copper_stock s WHERE s.local_balance > 0 AND {stock_where}) AS total_count
FROM (
  SELECT * FROM ordered
  ORDER BY date DESC, id DESC
  LIMIT :per_page OFFSET :offset
) sub
"""

            rows = db.session.execute(text(page_sql), params).mappings().all()
            # Extract total_count
            total_count = int(rows[0]['total_count']) if rows else 0
            # Convert rows to a list of simple dicts and build page_stock_ids
            page_stock_ids = [int(r['id']) for r in rows]
            # Map outputs_sum values for fast lookup
            outputs_sums = {int(r['id']): float(r['outputs_sum'] or 0) for r in rows}
            # Prepare filtered_outputs via a single ORM query (small result set)
            if page_stock_ids:
                outputs_q = db.session.query(CopperOutput.date, CopperOutput.output_kg).filter(CopperOutput.stock_id.in_(page_stock_ids))
                if start_date:
                    outputs_q = outputs_q.filter(CopperOutput.date >= start)
                if end_date:
                    outputs_q = outputs_q.filter(CopperOutput.date <= end)
                outputs_q = outputs_q.order_by(CopperOutput.date.desc())
                filtered_outputs = outputs_q.all()
            else:
                filtered_outputs = []
            # Create a lightweight pagination object and provide filtered_stocks
            from types import SimpleNamespace
            pages = (total_count + per_page - 1) // per_page if total_count else 1
            stocks_pagination = SimpleNamespace(pages=pages, total=total_count)
            # `filtered_stocks` used later for logging; set to rows mapping list
            filtered_stocks = rows
            timings['page_sql'] = 0.0
        except Exception:
            # Fallback to ORM paginate path on SQL failure to preserve behaviour
            logger.exception('filter_stocks: page SQL failed; falling back to ORM paginate')
            try:
                db.session.rollback()
            except Exception:
                pass
            stocks_local_q = stocks_query.filter(
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            )
            stocks_pagination = stocks_local_q.paginate(page=page, per_page=per_page, error_out=False)
            filtered_stocks = stocks_pagination.items
            page_stock_ids = [s.id for s in filtered_stocks]
            include_all = bool(data.get('include_all'))
            MAX_RETURN_ROWS = 2000
            if include_all and stocks_pagination.total > MAX_RETURN_ROWS:
                logger.warning("filter_stocks: include_all requested but total %d > max %d", stocks_pagination.total, MAX_RETURN_ROWS)
                return safe_jsonify({'error': 'Request would return too many rows; please narrow your filter or use the export endpoint.'}), 400

            outputs_query = CopperOutput.query.filter(CopperOutput.is_deleted.is_(False)).order_by(CopperOutput.date.desc())
            if start_date:
                outputs_query = outputs_query.filter(CopperOutput.date >= start)
            if end_date:
                outputs_query = outputs_query.filter(CopperOutput.date <= end)
            if page_stock_ids:
                outputs_query = outputs_query.filter(CopperOutput.stock_id.in_(page_stock_ids))
            filtered_outputs = outputs_query.all()
            from collections import defaultdict as _dd
            outputs_sums = _dd(float)
            if page_stock_ids:
                try:
                    rows = db.session.query(CopperOutput.stock_id, func.coalesce(func.sum(CopperOutput.output_kg), 0)).filter(CopperOutput.stock_id.in_(page_stock_ids))
                    if start_date:
                        rows = rows.filter(CopperOutput.date >= start)
                    if end_date:
                        rows = rows.filter(CopperOutput.date <= end)
                    rows = rows.group_by(CopperOutput.stock_id).all()
                    for sid, ssum in rows:
                        outputs_sums[sid] = float(ssum or 0)
                except Exception:
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
            # Unfiltered snapshot optimization: when there are no start/end/voucher
            # filters, read `StockAggregate` for lightweight `moyenne` values and
            # avoid expensive SUM(...) queries for those two totals.
            use_cache_key = not (start_date or end_date or voucher_no)

            # build filters for other aggregates (we keep existing queries)
            stock_filters = []
            stock_filters.append(CopperStock.is_deleted.is_(False))
            if start_date:
                stock_filters.append(CopperStock.date >= start)
            if end_date:
                stock_filters.append(CopperStock.date <= end)
            if voucher_no:
                stock_filters.append(CopperStock.voucher_no == voucher_no)

            output_filters = [CopperOutput.is_deleted.is_(False)]
            if start_date:
                output_filters.append(CopperOutput.date >= start)
            if end_date:
                output_filters.append(CopperOutput.date <= end)

            # Combine several scalar aggregates into a single DB round-trip
            # to reduce latency when backend↔DB RTT is significant.
            try:
                t_comb = time.perf_counter()
                from sqlalchemy import text

                stock_where = '1=1'
                output_where = '1=1'
                params = {}
                stock_where = 's.is_deleted IS FALSE'
                if start_date:
                    stock_where += ' AND s.date >= :start'
                    output_where += ' AND o.date >= :start'
                    params['start'] = start
                if end_date:
                    stock_where += ' AND s.date <= :end'
                    output_where += ' AND o.date <= :end'
                    params['end'] = end
                if voucher_no:
                    stock_where += ' AND s.voucher_no = :voucher_no'
                    params['voucher_no'] = voucher_no

                combined_sql = f"""
SELECT
    (SELECT COALESCE(SUM(s.input_kg),0) FROM copper_stock s WHERE {stock_where}) AS total_input,
    (SELECT COALESCE(COUNT(s.id),0) FROM copper_stock s WHERE {stock_where}) AS total_stocks,
    (SELECT COALESCE(SUM(o.output_kg),0) FROM copper_output o JOIN copper_stock s ON o.stock_id = s.id WHERE s.is_deleted IS FALSE AND o.is_deleted IS FALSE AND {output_where}) AS total_output,
  0 AS total_debt,
  0 AS total_sales,
    (SELECT COALESCE(SUM(s.net_balance),0) FROM copper_stock s WHERE {stock_where}) AS total_supplier_obligation,
        (SELECT COALESCE(SUM(COALESCE(sp.amount_rwf, sp.amount)),0) FROM supplier_payment sp JOIN copper_stock s ON sp.stock_id = s.id WHERE {stock_where}) AS total_payments
"""

                row = db.session.execute(text(combined_sql), params).fetchone()
                total_input = float(row.total_input or 0)
                total_stocks = int(row.total_stocks or 0)
                total_output = float(row.total_output or 0)
                total_debt = float(row.total_debt or 0)
                # Sales must match ledger truth (plans). Compute separately so we
                # don't couple this fast path to legacy copper_output monetary fields.
                try:
                    sales_q = (
                        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                        .filter(
                            BulkOutputPlan.mineral_type.in_(['copper', 'coltan']),
                            BulkOutputPlan.total_expected_amount.isnot(None),
                            BulkOutputPlan.total_expected_amount > 0,
                            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                        )
                    )
                    if start_date:
                        sales_q = sales_q.filter(BulkOutputPlan.created_at >= datetime.combine(start, datetime.min.time()))
                    if end_date:
                        sales_q = sales_q.filter(BulkOutputPlan.created_at <= datetime.combine(end, datetime.max.time()))
                    total_sales = float(sales_q.scalar() or 0.0)
                except Exception:
                    logger.exception('filter_stocks: total_sales aggregate from plans failed (combined SQL path)')
                    total_sales = float(row.total_sales or 0)
                total_supplier_obligation = float(row.total_supplier_obligation or 0)
                total_payments = float(row.total_payments or 0)
                timings['stock_aggregates'] = time.perf_counter() - t_comb
                # mark the other per-section keys as zero since we combined them
                timings['output_aggregates'] = 0.0
                timings['sales_aggregate'] = 0.0
                timings['supplier_obligation'] = 0.0
                timings['payments_aggregate'] = 0.0
            except Exception:
                # Fallback to individual queries if combined SQL fails
                t0 = time.perf_counter()
                total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).filter(*stock_filters).scalar() or 0
                total_stocks = db.session.query(func.coalesce(func.count(CopperStock.id), 0)).filter(*stock_filters).scalar() or 0
                timings['stock_aggregates'] = time.perf_counter() - t0

                t1 = time.perf_counter()
                total_output = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).filter(*output_filters).scalar() or 0
                total_debt = 0
                timings['output_aggregates'] = time.perf_counter() - t1

                t2 = time.perf_counter()
                # Total sales (monetary) must follow the same source of truth as debt.
                # We therefore sum BulkOutputPlan.total_expected_amount for the same
                # mineral type and date window.
                try:
                    sales_q = (
                        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                        .filter(
                            BulkOutputPlan.mineral_type.in_(['copper', 'coltan']),
                            BulkOutputPlan.total_expected_amount.isnot(None),
                            BulkOutputPlan.total_expected_amount > 0,
                            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                        )
                    )
                    if start_date:
                        sales_q = sales_q.filter(BulkOutputPlan.created_at >= datetime.combine(start, datetime.min.time()))
                    if end_date:
                        sales_q = sales_q.filter(BulkOutputPlan.created_at <= datetime.combine(end, datetime.max.time()))
                    total_sales = float(sales_q.scalar() or 0.0)
                except Exception:
                    logger.exception('filter_stocks: total_sales aggregate from plans failed')
                    total_sales = 0.0
                timings['sales_aggregate'] = time.perf_counter() - t2

                t3 = time.perf_counter()
                total_supplier_obligation = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(*stock_filters).scalar() or 0
                timings['supplier_obligation'] = time.perf_counter() - t3

                t4 = time.perf_counter()
                total_payments = db.session.query(func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0)).join(CopperStock, SupplierPayment.stock_id == CopperStock.id).filter(*stock_filters).scalar() or 0
                timings['payments_aggregate'] = time.perf_counter() - t4

            # Remaining stocks aggregates (only local_balance > 0)
            remaining_filters = list(stock_filters) + [
                CopperStock.local_balance > 0,
                CopperStock.is_deleted.is_(False),
            ]

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

            # Customer outstanding debt (plans - receipts) as single source of truth.
            try:
                plan_q = (
                    db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
                    .filter(
                        BulkOutputPlan.mineral_type.in_(['copper', 'coltan']),
                        BulkOutputPlan.total_expected_amount.isnot(None),
                        BulkOutputPlan.total_expected_amount > 0,
                        BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                    )
                )
                receipts_q = (
                    db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
                    .filter(CustomerReceipt.mineral_type.in_(['copper', 'coltan']))
                )
                if start_date:
                    plan_q = plan_q.filter(BulkOutputPlan.created_at >= datetime.combine(start, datetime.min.time()))
                    receipts_q = receipts_q.filter(CustomerReceipt.received_at >= datetime.combine(start, datetime.min.time()))
                if end_date:
                    plan_q = plan_q.filter(BulkOutputPlan.created_at <= datetime.combine(end, datetime.max.time()))
                    receipts_q = receipts_q.filter(CustomerReceipt.received_at <= datetime.combine(end, datetime.max.time()))
                expected_amt = plan_q.scalar() or 0.0
                paid_amt = receipts_q.scalar() or 0.0
                total_debt = float(expected_amt or 0.0) - float(paid_amt or 0.0)
            except Exception:
                logger.exception("filter_stocks: failed computing customer debt from plans/receipts")
                total_debt = 0.0

            # For moyenne values prefer reading the single-row StockAggregate when
            # the caller requested an unfiltered snapshot. Keep this minimal and
            # only replace the three SUM(...) calls used to compute moyenne.
            t7 = time.perf_counter()
            if use_cache_key:
                try:
                    from core.models import StockAggregate
                    agg = StockAggregate.get('copper')
                    if agg:
                        total_unit_percent = float(agg.total_weighted_percent or 0.0)
                        total_remaining_balance = float(agg.total_quantity or 0.0)
                        total_t_unity = float(agg.total_t_unity or 0.0)
                        moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                        moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
                        timings['remaining_aggregates'] = 0.0
                    else:
                        # fall back to DB SUMs if aggregate row missing
                        total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
                        total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
                        total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(*remaining_filters).scalar() or 0
                        moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                        moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
                        timings['remaining_aggregates'] = time.perf_counter() - t7
                except Exception:
                    total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
                    total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
                    total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(*remaining_filters).scalar() or 0
                    moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                    moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
                    timings['remaining_aggregates'] = time.perf_counter() - t7
            else:
                total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(*remaining_filters).scalar() or 0
                total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(*remaining_filters).scalar() or 0
                total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(*remaining_filters).scalar() or 0
                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
                timings['remaining_aggregates'] = time.perf_counter() - t7

            # Do not populate the global dashboard cache from filter endpoint;
            # keep cache writes owned by the dashboard view to avoid unexpected
            # cache state changes from filtered API calls.
        else:
            # Caller asked for a lightweight page (no heavy aggregates).
            # Ensure `remaining_filters` exists so cumulative totals can be
            # computed even when heavy aggregates are skipped.
            try:
                remaining_filters = []
                remaining_filters.append(CopperStock.is_deleted.is_(False))
                if start_date:
                    remaining_filters.append(CopperStock.date >= start)
                if end_date:
                    remaining_filters.append(CopperStock.date <= end)
                if voucher_no:
                    remaining_filters.append(CopperStock.voucher_no == voucher_no)
                remaining_filters = list(remaining_filters) + [CopperStock.local_balance > 0]
            except Exception:
                remaining_filters = [
                    CopperStock.local_balance > 0,
                    CopperStock.is_deleted.is_(False),
                ]
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
        # Populate cheap, unfiltered aggregates from cache or StockAggregate so
        # UI dashboard cards remain populated even when `include_aggregates=False`.
        try:
            if not (start_date or end_date or voucher_no):
                # Prefer the small in-process dashboard cache when available
                try:
                    cached = _get_dashboard_aggregates(ttl=10)
                    if cached:
                        total_input = float(cached.get('total_input', total_input or 0))
                        total_output = float(cached.get('total_output', total_output or 0))
                        total_debt = float(cached.get('total_debt', total_debt or 0))
                        total_sales = float(cached.get('total_sales', total_sales or 0))
                        total_supplier_obligation = float(cached.get('total_supplier_obligation', total_supplier_obligation or 0))
                        total_payments = float(cached.get('total_payments', total_payments or 0))
                        inventory_value = float(cached.get('inventory_value', inventory_value or 0))
                        cost_of_stock_sold = float(cached.get('cost_of_stock_sold', cost_of_stock_sold or 0))
                        gross_profit = float(cached.get('gross_profit', gross_profit or 0))
                        moyenne = float(cached.get('moyenne', moyenne or 0))
                        moyenne_nb = float(cached.get('moyenne_nb', moyenne_nb or 0))
                        total_unit_percent = float(cached.get('total_unit_percent', total_unit_percent or 0))
                        total_remaining_balance = float(cached.get('total_remaining_balance', total_remaining_balance or 0))
                        total_t_unity = float(cached.get('total_t_unity', total_t_unity or 0))
                    else:
                        # Fallback: read StockAggregate for moyenne values only
                        try:
                            from core.models import StockAggregate
                            agg = StockAggregate.get('copper')
                            if agg and agg.total_quantity:
                                total_unit_percent = float(agg.total_weighted_percent or 0.0)
                                total_remaining_balance = float(agg.total_quantity or 0.0)
                                total_t_unity = float(agg.total_t_unity or 0.0)
                                moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
                                moyenne_nb = (total_t_unity / total_remaining_balance) if total_remaining_balance else 0
                        except Exception:
                            pass

                        # As a last resort compute combined aggregates (heavier DB work)
                        try:
                            from sqlalchemy import text
                            combined_sql = """
SELECT
    (SELECT COALESCE(SUM(input_kg),0) FROM copper_stock WHERE is_deleted IS FALSE) AS total_input,
    (SELECT COALESCE(COUNT(id),0) FROM copper_stock WHERE local_balance > 0 AND is_deleted IS FALSE) AS total_stocks,
  (SELECT COALESCE(SUM(output_kg),0) FROM copper_output WHERE is_deleted IS FALSE) AS total_output,
  (SELECT COALESCE(SUM(debt_remaining),0) FROM copper_output WHERE is_deleted IS FALSE) AS total_debt,
  (SELECT COALESCE(SUM(output_amount),0) FROM copper_output WHERE is_deleted IS FALSE) AS total_sales,
    (SELECT COALESCE(SUM(net_balance),0) FROM copper_stock WHERE local_balance > 0 AND is_deleted IS FALSE) AS total_supplier_obligation,
        (SELECT COALESCE(SUM(COALESCE(sp.amount_rwf, sp.amount)),0) FROM supplier_payment sp JOIN copper_stock s ON sp.stock_id = s.id WHERE s.local_balance > 0 AND s.is_deleted IS FALSE) AS total_payments
"""
                            row = db.session.execute(text(combined_sql)).fetchone()
                            if row:
                                total_input = float(row.total_input or 0)
                                total_stocks = int(row.total_stocks or 0)
                                total_output = float(row.total_output or 0)
                                total_debt = float(row.total_debt or 0)
                                total_sales = float(row.total_sales or 0)
                                total_supplier_obligation = float(row.total_supplier_obligation or 0)
                                total_payments = float(row.total_payments or 0)

                            inv_row = db.session.execute(text("SELECT COALESCE(SUM(net_balance * local_balance / NULLIF(input_kg,0)),0) FROM copper_stock WHERE local_balance > 0 AND input_kg > 0 AND is_deleted IS FALSE")).scalar()
                            inventory_value = float(inv_row or 0)

                            cogs_q = db.session.query(
                                func.coalesce(
                                    func.sum(CopperOutput.output_kg * (CopperStock.net_balance / func.nullif(CopperStock.input_kg, 0))),
                                    0.0,
                                )
                            ).join(CopperStock, CopperOutput.stock_id == CopperStock.id).filter(CopperStock.is_deleted.is_(False))
                            cost_of_stock_sold = float(cogs_q.scalar() or 0.0)
                            gross_profit = (total_sales or 0) - (cost_of_stock_sold or 0)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        
        # Build stocks data for table (use SQL rows when the single-SQL
        # path succeeded, otherwise fall back to ORM `filtered_stocks`).
        t_build = time.perf_counter()
        stocks_data = []
        # If the page SQL executed, `rows` will be present as a list of mappings.
        if 'rows' in locals() and isinstance(rows, list) and rows and 'id' in rows[0]:
            for r in rows:
                dt = r.get('date')
                input_kg_val = float(r.get('input_kg') or 0)
                transport_val = float(r.get('transport_tag') or 0)
                tot_amount_tag_val = r.get('tot_amount_tag')
                computed_tag_total = transport_val * input_kg_val
                if tot_amount_tag_val is None or float(tot_amount_tag_val or 0) <= 0:
                    tot_amount_tag_val = computed_tag_total
                stocks_data.append({
                    'id': int(r.get('id')),
                    'date': dt.strftime('%Y-%m-%d') if dt is not None else None,
                    'voucher_no': r.get('voucher_no'),
                    'supplier': r.get('supplier'),
                    'input_kg': round(input_kg_val, 2),
                    'percentage': round(float(r.get('percentage') or 0), 2),
                    'nb': round(float(r.get('nb') or 0), 2),
                    'u_price': round(float(r.get('u_price') or 0), 2),
                    'amount': round(float(r.get('amount') or 0), 2),
                    'exchange': round(float(r.get('exchange') or 0), 2),
                    'transport_tag': round(transport_val, 2),
                    'tot_amount_tag': round(float(tot_amount_tag_val or 0), 2),
                    'rma': round(float(r.get('rma') or 0), 2),
                    'inkomane': round(float(r.get('inkomane') or 0), 2),
                    'local_balance': round(float(r.get('local_balance') or 0), 2),
                    'unit_percent': round(float(r.get('unit_percent') or 0), 4),
                    't_unity': round(float(r.get('t_unity') or 0), 2),
                    'rra_3_percent': round(float(r.get('rra_3_percent') or 0), 4),
                    'net_balance': round(float(r.get('net_balance') or 0), 2),
                    'total_balance': round(float(r.get('cumulative') or 0), 2),
                    'remaining': round((float(r.get('input_kg') or 0) - float(outputs_sums.get(int(r.get('id')), 0))) or 0, 2),
                    'moyenne': round(moyenne or 0, 4),
                    'moyenne_nb': round(moyenne_nb or 0, 4)
                })
        else:
            # Fallback to ORM objects
            for stock in filtered_stocks:
                input_kg_val = float(stock.input_kg or 0)
                transport_val = float(stock.transport_tag or 0)
                tot_amount_tag_val = stock.tot_amount_tag
                computed_tag_total = transport_val * input_kg_val
                if tot_amount_tag_val is None or float(tot_amount_tag_val or 0) <= 0:
                    tot_amount_tag_val = computed_tag_total
                stocks_data.append({
                    'id': stock.id,
                    'date': stock.date.strftime('%Y-%m-%d'),
                    'voucher_no': stock.voucher_no,
                    'supplier': stock.supplier,
                    'input_kg': round(input_kg_val, 2),
                    'percentage': round(stock.percentage or 0, 2),
                    'nb': round(stock.nb or 0, 2),
                    'u_price': round(stock.u_price or 0, 2),
                    'amount': round(stock.amount or 0, 2),
                    'exchange': round(stock.exchange or 0, 2),
                    'transport_tag': round(transport_val, 2),
                    'tot_amount_tag': round(float(tot_amount_tag_val or 0), 2),
                    'rma': round(stock.rma or 0, 2),
                    'inkomane': round(stock.inkomane or 0, 2),
                    'local_balance': round(stock.local_balance or 0, 2),
                    'unit_percent': round(stock.unit_percent or 0, 4),
                    't_unity': round(stock.t_unity or 0, 2),
                    'rra_3_percent': round(stock.rra_3_percent or 0, 4),
                    'net_balance': round(stock.net_balance or 0, 2),
                    'total_balance': round((stock.total_balance or 0), 2),
                    # Use pre-aggregated outputs per-stock to compute remaining
                    'remaining': round(((stock.input_kg or 0) - outputs_sums.get(stock.id, 0)) or 0, 2),
                    'moyenne': round(moyenne or 0, 4),
                    'moyenne_nb': round(moyenne_nb or 0, 4)
                })
        build_rows_time = time.perf_counter() - t_build

        # Build outputs data for charts (date vs output_kg) - robust to
        # both ORM objects and SQL row shapes.
        t_build_out = time.perf_counter()
        outputs_data = []
        for output in filtered_outputs:
            try:
                out_date = getattr(output, 'date')
                out_kg = getattr(output, 'output_kg')
            except Exception:
                try:
                    out_date = output[0]
                    out_kg = output[1]
                except Exception:
                    continue
            outputs_data.append({
                'date': out_date.strftime('%Y-%m-%d') if out_date is not None else None,
                'output_kg': round(out_kg or 0, 2)
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
        resp = safe_jsonify(payload)
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
        try:
            db.session.rollback()
        except Exception:
            logger.exception("filter_stocks: rollback failed")
        return safe_jsonify({'error': 'internal server error'}), 500
