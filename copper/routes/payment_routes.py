"""
Payment Routes
Handles supplier and worker payment recording for copper.
"""
import json

from datetime import datetime

from flask import render_template, redirect, url_for, flash, abort

from config import db
from copper.models import CopperStock, SupplierPayment, WorkerPayment, CopperSupplier, CopperAdvanceAllocation
from copper.forms import SupplierPaymentForm, WorkerPaymentForm
from copper import copper_bp
from core.auth import role_required
from flask import request
from sqlalchemy import func, or_
from utils import normalize_counterparty_name, generate_supplier_slug, close_name_matches, safe_jsonify, calculate_consolidated_supplier_remaining_balance, calculate_consolidated_supplier_remaining_balances, build_consolidated_supplier_choices


def _normalize_amount_to_rwf(amount, currency, exchange_rate):
    currency_code = (currency or 'RWF').upper()
    input_amount = float(amount or 0)
    rate = float(exchange_rate or 0)

    if currency_code == 'RWF':
        return input_amount, 1.0
    if currency_code == 'USD':
        if rate <= 0:
            raise ValueError('Exchange rate is required and must be greater than 0 for USD payments.')
        return input_amount * rate, rate
    raise ValueError(f'Unsupported currency: {currency_code}')


def _get_or_create_supplier_id(name):
    clean = (name or '').strip()
    if not clean:
        return None
    supplier = CopperSupplier.query.filter(func.lower(CopperSupplier.name) == clean.lower()).first()
    if supplier:
        return supplier.id
    supplier = CopperSupplier(name=clean)
    db.session.add(supplier)
    db.session.flush()
    return supplier.id


def calculate_supplier_remaining_balance(supplier_name):
    """
    SINGLE SOURCE OF TRUTH for calculating a supplier's remaining balance.
    
    Formula:
      Total Owed = SUM(stock.net_balance - allocations) for all stocks
      Total Paid = SUM(settlement payments only)
      Remaining = Total Owed - Total Paid
    
    This ensures consistency across all pages (ledger, debt tracking, advance form, etc.)
    """
    return calculate_consolidated_supplier_remaining_balance(supplier_name)


@copper_bp.route('/pay_supplier/search.json')
@role_required('accountant')
def pay_supplier_search():
    """AJAX endpoint: search suppliers by partial name and return remaining amount (Copper)."""
    # use safe_jsonify to ensure Decimal -> float conversion
    from copper.models import CopperStock, CopperSupplier

    q = (request.args.get('q') or '').strip()
    if not q:
        return safe_jsonify([])

    names = set()
    try:
        rows = db.session.query(CopperStock.supplier).filter(CopperStock.supplier.ilike(f"%{q}%")).distinct().all()
        names.update([r[0] for r in rows if r[0]])
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    try:
        rows2 = db.session.query(CopperSupplier.name).filter(CopperSupplier.name.ilike(f"%{q}%")).distinct().all()
        names.update([r[0] for r in rows2 if r[0]])
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    results = []
    for name in sorted(names):
        try:
            rem = calculate_supplier_remaining_balance(name)
            results.append({'supplier': name, 'remaining': f"{rem:,.2f}"})
        except Exception:
            results.append({'supplier': name, 'remaining': '0.00'})

    return safe_jsonify(results)


@copper_bp.route('/pay_supplier/stock-search.json')
@role_required('accountant')
def pay_supplier_stock_search():
    """AJAX endpoint: search copper supplier obligations by voucher or supplier name."""
    q = (request.args.get('q') or '').strip()
    query = db.session.query(CopperStock).filter(
        CopperStock.is_deleted.is_(False),
        CopperStock.net_balance > 0,
    )
    if q:
        like_q = f"%{q}%"
        query = query.filter(or_(CopperStock.voucher_no.ilike(like_q), CopperStock.supplier.ilike(like_q)))

    results = []
    for stock in query.order_by(CopperStock.date.desc()).limit(20).all():
        try:
            remaining = float(stock.remaining_to_pay() or 0.0)
        except Exception:
            remaining = 0.0
        if remaining <= 0:
            continue
        results.append({
            'id': stock.id,
            'display': f"{stock.voucher_no} - {stock.supplier}",
            'supplier': stock.supplier,
            'remaining': f"{remaining:,.2f} RWF",
        })

    return safe_jsonify(results)


@copper_bp.route('/supplier/payment/<int:payment_id>/receipt')
@role_required('accountant', 'cashier', 'boss', 'admin')
def supplier_receipt(payment_id):
    """
    Shows a printable receipt for a copper supplier payment.
    Shows ALL stocks for the supplier + cumulative balance.
    """
    from cassiterite.models import CassiteriteStock, CassiteriteAdvanceAllocation, CassiteriteSupplierPayment

    payment = SupplierPayment.query.get(payment_id)
    if not payment:
        abort(404)

    stock = CopperStock.query.get(payment.stock_id) if payment.stock_id else None
    supplier_name = (
        payment.supplier_name
        or (stock.supplier if stock else None)
        or (payment.supplier.name if getattr(payment, 'supplier', None) else None)
        or "Unknown"
    )

    # For ADVANCE payments (no stock linked)
    if payment.is_advance and not stock:
        allocated = db.session.query(
            func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0)
        ).filter(
            CopperAdvanceAllocation.supplier_payment_id == payment.id
        ).scalar() or 0.0
        
        remaining_before = 0.0
        remaining_after = float(payment.advance_remaining or 0.0)
        applied_to_stock = float(allocated)
        all_supplier_stocks = []
        all_supplier_stocks.extend(CopperStock.query.filter(
            CopperStock.supplier == supplier_name,
            CopperStock.is_deleted.is_(False)
        ).all())
        all_supplier_stocks.extend(CassiteriteStock.query.filter(
            CassiteriteStock.supplier == supplier_name,
            CassiteriteStock.is_deleted.is_(False)
        ).all())
        deductions_rows = []
        deductions_summary = {
            'gross': 0.0,
            'transport': 0.0,
            'rma': 0.0,
            'inkomane': 0.0,
            'rra_3_percent': 0.0,
            'net': 0.0,
        }
        for s in all_supplier_stocks:
            mineral_name = 'Coltan' if isinstance(s, CopperStock) else 'Cassiterite'
            gross = float(getattr(s, 'amount', 0.0) or 0.0)
            transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
            rma = float(getattr(s, 'rma', 0.0) or 0.0)
            inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
            rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
            net = float(getattr(s, 'net_balance', 0.0) or 0.0)
            deductions_rows.append({
                'mineral': mineral_name,
                'voucher_no': getattr(s, 'voucher_no', None) or str(getattr(s, 'id', '')),
                'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
                'gross': gross,
                'transport': transport,
                'rma': rma,
                'inkomane': inkomane,
                'rra_3_percent': rra,
                'net': net,
            })
            deductions_summary['gross'] += gross
            deductions_summary['transport'] += transport
            deductions_summary['rma'] += rma
            deductions_summary['inkomane'] += inkomane
            deductions_summary['rra_3_percent'] += rra
            deductions_summary['net'] += net
        previous_payments = []
        previous_payments_total = 0.0
    
    # For SETTLEMENT payments (linked to stock)
    else:
        # Fetch ALL stocks for this supplier across both minerals so the settlement receipt shows the full voucher/lot history.
        all_supplier_stocks = []
        all_supplier_stocks.extend(CopperStock.query.filter(
            CopperStock.supplier == supplier_name,
            CopperStock.is_deleted.is_(False)
        ).all())
        all_supplier_stocks.extend(CassiteriteStock.query.filter(
            CassiteriteStock.supplier == supplier_name,
            CassiteriteStock.is_deleted.is_(False)
        ).all())
        
        # Get allocations per stock (advance deductions)
        copper_stock_ids = [s.id for s in all_supplier_stocks if isinstance(s, CopperStock)]
        cassiterite_stock_ids = [s.id for s in all_supplier_stocks if isinstance(s, CassiteriteStock)]
        allocations = []
        if copper_stock_ids:
            allocations.extend(db.session.query(
                CopperAdvanceAllocation.stock_id,
                func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocated')
            ).filter(
                CopperAdvanceAllocation.stock_id.in_(copper_stock_ids)
            ).group_by(CopperAdvanceAllocation.stock_id).all())
        if cassiterite_stock_ids:
            allocations.extend(db.session.query(
                CassiteriteAdvanceAllocation.stock_id,
                func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0).label('allocated')
            ).filter(
                CassiteriteAdvanceAllocation.stock_id.in_(cassiterite_stock_ids)
            ).group_by(CassiteriteAdvanceAllocation.stock_id).all())

        allocation_map = {a.stock_id: float(a.allocated) for a in allocations}
        
        # Build deductions rows for ALL stocks
        deductions_rows = []
        deductions_summary = {
            'gross': 0.0,
            'transport': 0.0,
            'rma': 0.0,
            'inkomane': 0.0,
            'rra_3_percent': 0.0,
            'net': 0.0,
        }
        
        for s in all_supplier_stocks:
            mineral_name = 'Coltan' if isinstance(s, CopperStock) else 'Cassiterite'
            gross = float(getattr(s, 'amount', 0.0) or 0.0)
            transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
            rma = float(getattr(s, 'rma', 0.0) or 0.0)
            inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
            rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
            net = float(getattr(s, 'net_balance', 0.0) or 0.0)
            
            deductions_rows.append({
                'mineral': mineral_name,
                'voucher_no': getattr(s, 'voucher_no', None) or str(getattr(s, 'id', '')),
                'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
                'gross': gross,
                'transport': transport,
                'rma': rma,
                'inkomane': inkomane,
                'rra_3_percent': rra,
                'net': net,
            })
            
            deductions_summary['gross'] += gross
            deductions_summary['transport'] += transport
            deductions_summary['rma'] += rma
            deductions_summary['inkomane'] += inkomane
            deductions_summary['rra_3_percent'] += rra
            deductions_summary['net'] += net
        
        # Supplier-wide remaining balance
        remaining_before = calculate_supplier_remaining_balance(supplier_name)
        payment_amount = float(payment.amount_rwf or payment.amount or 0.0)
        remaining_after = float(remaining_before or 0.0) - payment_amount
        remaining_after = max(remaining_after, 0.0)
        applied_to_stock = 0.0
        
        # All payments to this supplier across both minerals, not just this stock.
        previous_payments = []
        previous_payments.extend(SupplierPayment.query.filter(
            SupplierPayment.supplier_name == supplier_name,
            SupplierPayment.is_deleted.is_(False),
            SupplierPayment.is_advance.is_(False),
            SupplierPayment.id != payment.id,
        ).order_by(SupplierPayment.paid_at.desc()).all())
        previous_payments.extend(CassiteriteSupplierPayment.query.filter(
            CassiteriteSupplierPayment.supplier_name == supplier_name,
            CassiteriteSupplierPayment.is_deleted.is_(False),
            CassiteriteSupplierPayment.is_advance.is_(False),
            CassiteriteSupplierPayment.id != payment.id,
        ).order_by(CassiteriteSupplierPayment.paid_at.desc()).all())
        
        previous_payments_total = float(
            sum(float(p.amount_rwf or p.amount or 0.0) for p in previous_payments)
        )

    template_name = 'receipts/advance_payment_form.html' if bool(payment.is_advance or not payment.stock_id) else 'receipts/settlement_payment_form.html'

    return render_template(
        template_name,
        payment=payment,
        supplier_name=supplier_name,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
        applied_to_stock=applied_to_stock,
        is_advance=bool(payment.is_advance or not payment.stock_id),
        stock=stock,
        deductions_rows=deductions_rows,
        deductions_summary=deductions_summary,
        previous_payments=previous_payments,
        previous_payments_total=previous_payments_total,
        currency=payment.currency or 'RWF',
        exchange_rate=payment.exchange_rate or 1.0,
    )


@copper_bp.route('/worker/payment/<int:payment_id>/receipt')
@role_required('accountant', 'cashier', 'boss', 'admin')
def worker_receipt(payment_id):
    """
    Shows a printable receipt for a copper worker payment.
    """
    payment = WorkerPayment.query.get(payment_id)
    if not payment:
        abort(404)
    return render_template('receipts/copper_worker_receipt.html', payment=payment)


@copper_bp.route('/pay_supplier', methods=['GET', 'POST'])
@role_required('accountant')
def pay_supplier():
    """Record supplier payments for copper stocks."""
    from flask import current_app
    from flask_login import current_user
    from core.models import PaymentReview, PaymentReviewStatus, create_notification, User
    from utils import send_brevo_email_async
    from copper.forms import SupplierPaymentForm

    form = SupplierPaymentForm()
    selected_stock_label = ''
    if request.method == 'GET':
        requested_kind = (request.args.get('payment_kind') or '').strip().lower()
        if requested_kind == 'advance':
            return redirect(url_for('copper.pay_supplier_advance'))
        form.payment_kind.data = 'settlement'

    if request.method == 'POST':
        try:
            selected_stock_id = int(request.form.get('stock_id') or 0)
        except (TypeError, ValueError):
            selected_stock_id = 0
        if selected_stock_id:
            selected_stock = CopperStock.query.get(selected_stock_id)
            if selected_stock:
                selected_stock_label = f"{selected_stock.voucher_no} - {selected_stock.supplier}"

    # populate stock choices - select only required columns, compute remaining via grouped aggregate
    stock_rows = db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.supplier, CopperStock.net_balance).filter(
        CopperStock.net_balance > 0,
        CopperStock.is_deleted.is_(False),
    ).order_by(CopperStock.date.desc()).all()
    stock_ids = [r.id for r in stock_rows]
    if stock_ids:
        paid_rows = (
            db.session.query(
                SupplierPayment.stock_id,
                func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0).label('paid')
            )
            .filter(
                SupplierPayment.stock_id.in_(stock_ids),
                SupplierPayment.is_deleted.is_(False),
            )
            .group_by(SupplierPayment.stock_id)
            .all()
        )
        paid_map = {r.stock_id: float(r.paid) for r in paid_rows}

        allocation_rows = (
            db.session.query(
                CopperAdvanceAllocation.stock_id,
                func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocated')
            )
            .filter(CopperAdvanceAllocation.stock_id.in_(stock_ids))
            .group_by(CopperAdvanceAllocation.stock_id)
            .all()
        )
        allocation_map = {r.stock_id: float(r.allocated) for r in allocation_rows}
    else:
        paid_map = {}
        allocation_map = {}

    supplier_names = sorted({(r.supplier or '').strip() for r in stock_rows if (r.supplier or '').strip()})
    form.existing_supplier.choices = [('', 'Select existing supplier')] + [(s, s) for s in supplier_names]

    form.stock_id.choices = []
    for r in stock_rows:
        remaining = (r.net_balance or 0) - paid_map.get(r.id, 0.0) - allocation_map.get(r.id, 0.0)
        if remaining > 0:
            form.stock_id.choices.append((r.id, f"{r.voucher_no} - {r.supplier} - Remaining: {remaining:,.2f} RWF"))

    if form.validate_on_submit():
        input_amount = float(form.amount.data or 0)
        currency = (form.currency.data or 'RWF').upper()
        exchange_rate_input = form.exchange_rate.data
        requested_paid_at = form.paid_at.data or datetime.utcnow()
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            pending_reviews = PaymentReview.query.filter_by(
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews, selected_stock_label=selected_stock_label)
        payment_kind = 'settlement'

        try:
            payment_supplier = None
            stock = None

            if not form.stock_id.data:
                flash('Please select a supplier obligation from the suggestions.', 'danger')
                pending_reviews = PaymentReview.query.filter_by(
                    created_by_id=getattr(current_user, 'id', None),
                    status=PaymentReviewStatus.PENDING_REVIEW.value,
                ).order_by(PaymentReview.created_at.desc()).limit(10).all()
                return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews, selected_stock_label=selected_stock_label)

            stock = CopperStock.query.get_or_404(form.stock_id.data)
            payment_supplier = stock.supplier
            supplier_id = _get_or_create_supplier_id(payment_supplier)
            stock_remaining = float(stock.remaining_to_pay() or 0.0)
            if amount_rwf > stock_remaining:
                flash(f"Payment exceeds remaining balance ({stock_remaining:,.2f} RWF).", "danger")
                pending_reviews = PaymentReview.query.filter_by(
                    created_by_id=getattr(current_user, 'id', None),
                    status=PaymentReviewStatus.PENDING_REVIEW.value,
                ).order_by(PaymentReview.created_at.desc()).limit(10).all()
                return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews, selected_stock_label=selected_stock_label)
            payload = {
                "payment_kind": payment_kind,
                "stock_id": getattr(stock, "id", None),
                "supplier_name": payment_supplier,
                "supplier_id": supplier_id,
                "method": form.method.data,
                "reference": form.reference.data,
                "note": form.note.data,
                "currency": currency,
                "exchange_rate": exchange_rate,
                "amount_input": input_amount,
                "amount_rwf": amount_rwf,
            }
            review = PaymentReview(
                mineral_type='coltan',
                type='utanga ibicuruzwa',
                customer=payment_supplier,
                amount=amount_rwf,
                currency='RWF',
                payment_id=None,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment='kwishyura supplier',
                request_payload=json.dumps(payload),
            )
            db.session.add(review)
            db.session.commit()

            # in-app notification
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='kwishyura utanga ibicuruzwa',
                    message=f"Hasabwe kwemeza: Kwishyura utanga ibicuruzwa kuri Coltan - {payment_supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}).",
                    related_type='supplier_payment',
                    related_id=review.id,
                )
            # Persist in-app notification before attempting email
            db.session.commit()

            # email notification (non-blocking)
            boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
            payment_details = (
                f"utanga amabuye: {payment_supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}), Uburyo: {form.method.data}, "
                f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
            )
            subject = "Saba Kwemezwa: Kwishyura utanga Ibicuruzwa (Coltan)"
            html_content = (
                "<p>Nyakubahwa Muyobozi,</p>"
                f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) "
                f"yasabye kwemeza ubwishyu bukurikira kuri Coltan:</p>"
                f"<p>{payment_details}</p>"
                "<p>Nyamuneka musuzume kandi mwemeze. Mujye muri Sisiteme kwemeza iki gikorwa</p>"
                "<p>Murakoze,<br>Urumuli Smart System</p>"
            )
            try:
                send_brevo_email_async(subject, html_content, boss_email)
            except Exception:
                import logging
                logging.exception("Failed to send supplier payment email")
                flash("Email notification failed; in-app notification saved.", "warning")

            flash(f"Payment request of {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}) sent for boss approval ({payment_supplier}).", "success")
            return redirect(url_for('copper.pay_supplier'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving payment: {e}", "danger")
            pending_reviews = PaymentReview.query.filter_by(
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews, selected_stock_label=selected_stock_label)

    # GET or not-submitted
    # Build supplier summaries consolidated across minerals (copper + cassiterite).
    supplier_query = (request.args.get('supplier') or '').strip()
    per_page = 15
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    # Copper (Coltan) owed per supplier = sum(net_balance) - sum(allocations)
    copper_stock_net = (
        db.session.query(
            CopperStock.supplier.label('supplier'),
            func.coalesce(func.sum(CopperStock.net_balance), 0).label('total_net'),
        )
        .filter(CopperStock.is_deleted.is_(False), CopperStock.net_balance > 0)
        .group_by(CopperStock.supplier)
        .all()
    )
    copper_alloc = (
        db.session.query(
            CopperStock.supplier.label('supplier'),
            func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('total_alloc'),
        )
        .join(CopperStock, CopperStock.id == CopperAdvanceAllocation.stock_id)
        .filter(CopperStock.is_deleted.is_(False))
        .group_by(CopperStock.supplier)
        .all()
    )
    copper_alloc_map = {(r.supplier or '').strip(): float(r.total_alloc or 0.0) for r in copper_alloc}

    copper_paid = (
        db.session.query(
            func.coalesce(CopperStock.supplier, SupplierPayment.supplier_name).label('supplier'),
            func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0).label('total_paid'),
            func.max(SupplierPayment.paid_at).label('latest_paid_at'),
        )
        .outerjoin(CopperStock, CopperStock.id == SupplierPayment.stock_id)
        .filter(SupplierPayment.is_deleted.is_(False), SupplierPayment.is_advance.is_(False))
        .group_by(func.coalesce(CopperStock.supplier, SupplierPayment.supplier_name))
        .all()
    )

    # Cassiterite owed per supplier = sum(balance_to_pay)
    try:
        from cassiterite.models import CassiteriteStock
        from cassiterite.models.payment import CassiteriteSupplierPayment
    except Exception:
        CassiteriteStock = None
        CassiteriteSupplierPayment = None

    cass_stock_net = []
    cass_paid = []
    if CassiteriteStock is not None and CassiteriteSupplierPayment is not None:
        cass_stock_net = (
            db.session.query(
                CassiteriteStock.supplier.label('supplier'),
                func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0).label('total_net'),
            )
            .filter(CassiteriteStock.is_deleted.is_(False), CassiteriteStock.balance_to_pay > 0)
            .group_by(CassiteriteStock.supplier)
            .all()
        )
        cass_paid = (
            db.session.query(
                func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name).label('supplier'),
                func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0).label('total_paid'),
                func.max(CassiteriteSupplierPayment.paid_at).label('latest_paid_at'),
            )
            .outerjoin(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
            .filter(CassiteriteSupplierPayment.is_deleted.is_(False), CassiteriteSupplierPayment.is_advance.is_(False))
            .group_by(func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name))
            .all()
        )

    # Include suppliers that exist only in unified advances (e.g., historical imports)
    # so table rows stay consistent with autocomplete suggestions.
    from core.models import UnifiedSupplierAdvance

    advance_suppliers = (
        db.session.query(
            UnifiedSupplierAdvance.supplier_name_norm.label('supplier_norm'),
            func.max(UnifiedSupplierAdvance.supplier_name).label('supplier_name'),
            func.max(UnifiedSupplierAdvance.paid_at).label('latest_paid_at'),
        )
        .filter(
            UnifiedSupplierAdvance.is_deleted.is_(False),
            UnifiedSupplierAdvance.supplier_name_norm.isnot(None),
            func.trim(UnifiedSupplierAdvance.supplier_name_norm) != '',
        )
        .group_by(UnifiedSupplierAdvance.supplier_name_norm)
        .all()
    )

    # Merge by normalized supplier name so the user sees a single row per supplier.
    merged = {}
    def _ensure(name: str | None) -> dict:
        clean = (name or '').strip()
        norm = normalize_counterparty_name(clean)
        if not norm:
            return {}
        row = merged.get(norm)
        if not row:
            merged[norm] = {
                'supplier': clean,
                'net_balance': 0.0,
                'total_paid': 0.0,
                'latest_paid_at': None,
                'minerals': set(),
            }
            row = merged[norm]
        if clean and len(clean) > len(row.get('supplier') or ''):
            row['supplier'] = clean
        return row

    for r in copper_stock_net:
        name = (r.supplier or '').strip()
        row = _ensure(name)
        if not row:
            continue
        owed = float(r.total_net or 0.0) - float(copper_alloc_map.get(name, 0.0) or 0.0)
        row['net_balance'] += max(owed, 0.0)
        row['minerals'].add('Coltan')

    for r in cass_stock_net:
        name = (r.supplier or '').strip()
        row = _ensure(name)
        if not row:
            continue
        row['net_balance'] += max(float(r.total_net or 0.0), 0.0)
        row['minerals'].add('Cassiterite')

    for r in copper_paid:
        name = (r.supplier or '').strip()
        row = _ensure(name)
        if not row:
            continue
        row['total_paid'] += max(float(r.total_paid or 0.0), 0.0)
        if r.latest_paid_at and (row['latest_paid_at'] is None or r.latest_paid_at > row['latest_paid_at']):
            row['latest_paid_at'] = r.latest_paid_at
        row['minerals'].add('Coltan')

    for r in cass_paid:
        name = (r.supplier or '').strip()
        row = _ensure(name)
        if not row:
            continue
        row['total_paid'] += max(float(r.total_paid or 0.0), 0.0)
        if r.latest_paid_at and (row['latest_paid_at'] is None or r.latest_paid_at > row['latest_paid_at']):
            row['latest_paid_at'] = r.latest_paid_at
        row['minerals'].add('Cassiterite')

    for r in advance_suppliers:
        name = (r.supplier_name or '').strip() or (r.supplier_norm or '').strip()
        row = _ensure(name)
        if not row:
            continue
        if r.latest_paid_at and (row['latest_paid_at'] is None or r.latest_paid_at > row['latest_paid_at']):
            row['latest_paid_at'] = r.latest_paid_at
        row['minerals'].add('Advance')

    merged_supplier_names = [
        (row.get('supplier') or '').strip()
        for row in merged.values()
        if (row.get('supplier') or '').strip()
    ]
    remaining_map = calculate_consolidated_supplier_remaining_balances(merged_supplier_names)

    rows = []
    for norm, r in merged.items():
        supplier_name = (r.get('supplier') or '').strip()
        if not supplier_name:
            continue
        if supplier_query and supplier_query.lower() not in supplier_name.lower():
            continue
        net_balance = float(r.get('net_balance') or 0.0)
        total_paid = float(r.get('total_paid') or 0.0)
        remaining = float(remaining_map.get(' '.join(supplier_name.lower().split()), net_balance - total_paid))
        if abs(remaining) <= 0.0001 and net_balance <= 0 and total_paid <= 0:
            continue
        rows.append({
            'supplier': supplier_name,
            'net_balance': net_balance,
            'total_paid': total_paid,
            'remaining': remaining,
            'latest_paid_at': r.get('latest_paid_at'),
            'minerals': ', '.join(sorted(list(r.get('minerals') or set())))
        })

    rows.sort(key=lambda x: (
        x.get('latest_paid_at') is None,
        x.get('latest_paid_at') or datetime.min,
        (x.get('supplier') or '').lower(),
    ), reverse=False)

    total_suppliers = len(rows)
    total_pages = (total_suppliers + per_page - 1) // per_page if total_suppliers else 1
    if page > total_pages:
        page = total_pages
    offset_val = (page - 1) * per_page
    rows = rows[offset_val:offset_val + per_page]
    # Build a map of recent payments for suppliers on this page so templates
    # can render per-supplier View/Print receipt links (restore regression).
    supplier_summaries = []
    recent_suppliers = []

    page_supplier_names = [((r.get('supplier') or '').strip()) for r in rows if (r.get('supplier') or '').strip()]
    payments_map = {}
    if page_supplier_names:
        payment_rows = (
            db.session.query(SupplierPayment, CopperStock.supplier.label('stock_supplier'))
            .outerjoin(CopperStock, CopperStock.id == SupplierPayment.stock_id)
            .filter(SupplierPayment.is_deleted.is_(False))
            .filter(func.coalesce(CopperStock.supplier, SupplierPayment.supplier_name).in_(page_supplier_names))
            .order_by(SupplierPayment.paid_at.desc(), SupplierPayment.id.desc())
            .all()
        )
        for payment, stock_supplier in payment_rows:
            key = (payment.supplier_name or stock_supplier or '').strip()
            if not key:
                continue
            payments_map.setdefault(key, []).append({
                'id': payment.id,
                'date': payment.paid_at.strftime('%Y-%m-%d %H:%M') if getattr(payment, 'paid_at', None) else '',
                'currency': (getattr(payment, 'currency', None) or 'RWF').upper(),
                'amount_input': float(getattr(payment, 'input_amount', None) or getattr(payment, 'amount', 0) or 0),
                'amount_rwf': float(getattr(payment, 'amount_rwf', None) or getattr(payment, 'amount', 0) or 0),
            })
        # limit to most recent 5 per supplier
        for k in list(payments_map.keys()):
            payments_map[k] = payments_map[k][:5]

    for r in rows:
        supplier_name = (r.get('supplier') or '').strip()
        if not supplier_name:
            continue
        net_balance = float(r.get('net_balance') or 0.0)
        total_paid = float(r.get('total_paid') or 0.0)
        remaining = float(r.get('remaining') or 0.0)
        latest_paid_at = r.get('latest_paid_at')
        supplier_norm = ' '.join((supplier_name or '').strip().lower().split())
        supplier_slug = '-'.join(supplier_norm.split()) if supplier_norm else ''
        supplier_summaries.append({
            'supplier': supplier_name,
            'supplier_norm': supplier_norm,
            'supplier_slug': supplier_slug,
            'vouchers': r.get('minerals') or '',
            'net_balance': net_balance,
            'total_paid': total_paid,
            'remaining': remaining,
            'latest_paid_at': latest_paid_at,
            'payments': payments_map.get(supplier_name, [])
        })
        recent_suppliers.append({'supplier': supplier_name, 'latest_paid_at': latest_paid_at})

    suppliers_pagination = {
        'page': page,
        'per_page': per_page,
        'total': total_suppliers,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_num': page - 1,
        'next_num': page + 1,
        'query': supplier_query,
    }

    pending_reviews = PaymentReview.query.filter_by(
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
    ).order_by(PaymentReview.created_at.desc()).limit(10).all()
    return render_template(
        'copper/pay_supplier.html',
        form=form,
        supplier_summaries=supplier_summaries,
        pending_reviews=pending_reviews,
        recent_suppliers=recent_suppliers,
        suppliers_pagination=suppliers_pagination,
        supplier_query=supplier_query,
    )


@copper_bp.route('/pay_supplier/advance', methods=['GET', 'POST'])
@role_required('accountant')
def pay_supplier_advance():
    """Record advance supplier payments for copper stocks."""
    from flask_login import current_user
    from core.models import PaymentReview, PaymentReviewStatus, create_notification, User
    from utils import send_brevo_email_async
    from copper.forms import SupplierPaymentForm

    form = SupplierPaymentForm()
    form.payment_kind.data = 'advance'

    stock_rows = db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.supplier, CopperStock.net_balance).filter(
        CopperStock.net_balance > 0,
        CopperStock.is_deleted.is_(False),
    ).order_by(CopperStock.date.desc()).all()
    stock_ids = [r.id for r in stock_rows]
    
    # Step 1: Calculate allocations for each stock
    if stock_ids:
        allocation_rows = db.session.query(
            CopperAdvanceAllocation.stock_id,
            func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocated')
        ).filter(
            CopperAdvanceAllocation.stock_id.in_(stock_ids)
        ).group_by(CopperAdvanceAllocation.stock_id).all()
        allocation_map = {a.stock_id: float(a.allocated) for a in allocation_rows}
    else:
        allocation_map = {}
    
    # Step 2: Build supplier summary with CORRECT "owed" = net_balance - allocations
    supplier_summary_map = {}
    supplier_names = sorted({(r.supplier or '').strip() for r in stock_rows if (r.supplier or '').strip()})
    remaining_map = calculate_consolidated_supplier_remaining_balances(supplier_names)
    for row in stock_rows:
        key = (row.supplier or '').strip()
        if not key:
            continue
        summary = supplier_summary_map.setdefault(key, {'supplier': key, 'owed': 0.0, 'paid': 0.0, 'remaining': 0.0})
        # Update: Calculate remaining balance using consolidated supplier balance
        net_owed = float(remaining_map.get(' '.join(key.lower().split()), 0.0))
        summary['owed'] += net_owed

    # Step 3: Add only SETTLEMENT payments (NOT advances) to "paid"
    if supplier_names:
        payment_filters = [
            SupplierPayment.is_deleted.is_(False),
            SupplierPayment.is_advance.is_(False),  # ← ONLY settlements
        ]
        supplier_conditions = []
        if stock_ids:
            supplier_conditions.append(SupplierPayment.stock_id.in_(stock_ids))
        supplier_conditions.append(SupplierPayment.supplier_name.in_(supplier_names))
        payment_rows = (
            db.session.query(SupplierPayment, CopperStock.supplier.label('stock_supplier'))
            .outerjoin(CopperStock, CopperStock.id == SupplierPayment.stock_id)
            .filter(*payment_filters)
            .filter(or_(*supplier_conditions))
            .order_by(SupplierPayment.paid_at.desc(), SupplierPayment.id.desc())
            .all()
        )
        for payment, stock_supplier in payment_rows:
            key = (payment.supplier_name or stock_supplier or '').strip()
            if not key or key not in supplier_summary_map:
                continue
            supplier_summary_map[key]['paid'] += float(payment.amount_rwf or payment.amount or 0)

    # Step 4: Calculate remaining
    for summary in supplier_summary_map.values():
        summary['remaining'] = float(remaining_map.get(' '.join(summary['supplier'].lower().split()), 0.0))

    form.existing_supplier.choices = build_consolidated_supplier_choices()
    form.stock_id.choices = []

    page = request.args.get('page', 1, type=int)
    recent_advances = (
        SupplierPayment.query
        .filter(
            SupplierPayment.is_advance.is_(True),
            SupplierPayment.is_deleted.is_(False),
        )
        .order_by(SupplierPayment.paid_at.desc(), SupplierPayment.id.desc())
        .paginate(page=page, per_page=10, error_out=False)
    )

    if form.validate_on_submit():
        input_amount = float(form.amount.data or 0)
        currency = (form.currency.data or 'RWF').upper()
        exchange_rate_input = form.exchange_rate.data
        requested_paid_at = form.paid_at.data or datetime.utcnow()
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            pending_reviews = PaymentReview.query.filter_by(created_by_id=getattr(current_user, 'id', None), status=PaymentReviewStatus.PENDING_REVIEW.value).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)
        typed_new = (form.new_supplier.data or '').strip()
        selected_existing = (form.existing_supplier.data or '').strip()
        supplier = (typed_new or selected_existing or '').strip()
        if not supplier:
            flash('Please select an existing supplier or enter a new supplier name for advance payment.', 'danger')
            pending_reviews = PaymentReview.query.filter_by(created_by_id=getattr(current_user, 'id', None), status=PaymentReviewStatus.PENDING_REVIEW.value).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)

        # Guard: prevent accidental near-duplicate supplier identities.
        if typed_new:
            confirm_new_supplier = (request.form.get('confirm_new_supplier') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
            try:
                from copper.models import CopperSupplier
                from cassiterite.models import CassiteriteSupplier
                existing_names = [r[0] for r in db.session.query(CopperSupplier.name).filter(CopperSupplier.is_deleted.is_(False)).all()]
                existing_names += [r[0] for r in db.session.query(CassiteriteSupplier.name).filter(CassiteriteSupplier.is_deleted.is_(False)).all()]
            except Exception:
                existing_names = []
            norm_new = normalize_counterparty_name(supplier)
            exact_exists = any(normalize_counterparty_name(n) == norm_new for n in existing_names)
            if not exact_exists:
                close = close_name_matches(supplier, existing_names, limit=5, cutoff=0.86)
                if close and not confirm_new_supplier:
                    flash(
                        f"Supplier name looks similar to existing supplier(s): {', '.join(close[:3])}. Select the existing supplier or confirm you want to create a new one.",
                        'warning',
                    )
                    pending_reviews = PaymentReview.query.filter_by(created_by_id=getattr(current_user, 'id', None), status=PaymentReviewStatus.PENDING_REVIEW.value).order_by(PaymentReview.created_at.desc()).limit(10).all()
                    return render_template('copper/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)

        supplier_id = _get_or_create_supplier_id(supplier)
        payload = {
            'payment_kind': 'advance',
            'stock_id': None,
            'supplier_name': supplier,
            'supplier_id': supplier_id,
            'method': form.method.data,
            'reference': form.reference.data,
            'note': form.note.data,
            'currency': currency,
            'exchange_rate': exchange_rate,
            'amount_input': input_amount,
            'amount_rwf': amount_rwf,
            'paid_at': requested_paid_at.strftime('%Y-%m-%dT%H:%M'),
        }
        review = PaymentReview(
            mineral_type='coltan',
            type='utanga ibicuruzwa',
            customer=supplier,
            amount=amount_rwf,
            currency='RWF',
            payment_id=None,
            created_by_id=getattr(current_user, 'id', None),
            boss_comment='kwishyura advance',
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.commit()

        boss_user = User.query.filter_by(role='boss').first()
        if boss_user:
            create_notification(
                user_id=boss_user.id,
                type_='kwishyura advance',
                message=f'Hasabwe kwemeza: Kwishyura advance supplier - {supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}).',
                related_type='supplier_payment',
                related_id=review.id,
            )
        db.session.commit()

        boss_email = [boss_user.email] if boss_user and boss_user.email else ['boss@example.com']
        try:
            send_brevo_email_async(
                'Gusaba kwemeza igikorwa: Kwishyura advance supplier (Coltan)',
                (
                    '<p>Nyakubahwa Muyobozi,</p>'
                    f'<p>Umucungamutungo {getattr(current_user, "username", "Unknown")} ({getattr(current_user, "email", "Unknown")}) yasabye kwemeza advance supplier:</p>'
                    f'<p>Supplier: {supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}), Itariki: {requested_paid_at.strftime("%Y-%m-%d %H:%M")}, Uburyo: {form.method.data}, Reference: {form.reference.data}, Impamvu: {form.note.data}</p>'
                    '<p>Murakoze,<br>Urumuli Smart System</p>'
                ),
                boss_email,
            )
        except Exception:
            flash('Email notification failed; in-app notification saved.', 'warning')

        flash(f'Advance payment request of {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}) sent for boss approval ({supplier}).', 'success')
        return redirect(url_for('copper.pay_supplier_advance'))

    pending_reviews = PaymentReview.query.filter_by(
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
    ).order_by(PaymentReview.created_at.desc()).limit(10).all()
    return render_template(
        'copper/pay_supplier_advance.html',
        form=form,
        pending_reviews=pending_reviews,
        recent_advances=recent_advances,
    )


@copper_bp.route('/pay_supplier_advance/historical', methods=['GET', 'POST'])
@role_required('accountant', 'admin')
def pay_supplier_advance_historical():
    """Record a historical supplier advance (opening-balance import).

    This bypasses boss approval and cashier disbursement and creates
    a unified advance row marked `is_historical`.
    """
    from flask_login import current_user
    from copper.models import CopperSupplier, CopperStock
    from cassiterite.models import CassiteriteSupplier, CassiteriteStock
    from core.models import UnifiedSupplierAdvance

    form = SupplierPaymentForm()

    page = request.args.get('page', 1, type=int)
    recent_advances = (
        SupplierPayment.query
        .filter(
            SupplierPayment.is_advance.is_(True),
            SupplierPayment.is_deleted.is_(False),
        )
        .order_by(SupplierPayment.paid_at.desc(), SupplierPayment.id.desc())
        .paginate(page=page, per_page=10, error_out=False)
    )

    form.existing_supplier.choices = build_consolidated_supplier_choices()

    if form.validate_on_submit():
        input_amount = float(form.amount.data or 0)
        currency = (form.currency.data or 'RWF').upper()
        exchange_rate_input = form.exchange_rate.data
        requested_paid_at = form.paid_at.data or datetime.utcnow()
        import_batch = (request.form.get('import_batch') or '').strip()
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            return render_template('copper/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)

        typed_new = (form.new_supplier.data or '').strip()
        selected_existing = (form.existing_supplier.data or '').strip()
        supplier = (typed_new or selected_existing or '').strip()
        if not supplier:
            flash('Please select an existing supplier or enter a new supplier name for advance payment.', 'danger')
            return render_template('copper/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)

        supplier_id = _get_or_create_supplier_id(supplier)

        # Create a supplier payment record for history (optional)
        payment = SupplierPayment(
            stock_id=None,
            supplier_id=supplier_id,
            supplier_name=supplier,
            amount=amount_rwf,
            input_amount=input_amount,
            currency=currency,
            exchange_rate=exchange_rate,
            amount_rwf=amount_rwf,
            method=form.method.data,
            reference=form.reference.data,
            note=(form.note.data or '') + (f" [import_batch:{import_batch}]" if import_batch else ''),
            payment_type='ADVANCE',
            is_advance=True,
            approval_status='APPROVED',
            disbursement_status='DISBURSED',
            advance_remaining=float(amount_rwf or 0.0),
            created_by_id=getattr(current_user, 'id', None),
        )
        payment.paid_at = requested_paid_at
        db.session.add(payment)
        db.session.flush()

        # Create unified advance marked historical using canonical supplier keys.
        unified = UnifiedSupplierAdvance(
            supplier_name=supplier,
            supplier_name_norm=normalize_counterparty_name(supplier),
            supplier_slug=generate_supplier_slug(supplier),
            source_mineral_type='historical',
            source_payment_id=None,
            input_amount=float(input_amount) if input_amount is not None else None,
            currency=(currency or 'RWF'),
            exchange_rate=float(exchange_rate or 1.0),
            amount_rwf=float(amount_rwf or 0.0),
            paid_at=payment.paid_at,
            method=form.method.data,
            reference=form.reference.data,
            note=(form.note.data or '') + (f" [import_batch:{import_batch}]" if import_batch else ''),
            advance_remaining=float(amount_rwf or 0.0),
            created_by_id=getattr(current_user, 'id', None),
            is_historical=True,
        )
        db.session.add(unified)
        db.session.flush()

        db.session.commit()
        flash(f'Historical advance of {amount_rwf:,.2f} RWF recorded for {supplier}.', 'success')
        return redirect(url_for('copper.pay_supplier_advance_historical'))

    return render_template('copper/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)


@copper_bp.route('/supplier/settlement/search.json')
@role_required('accountant')
def supplier_settlement_search():
    """AJAX endpoint: get all suppliers with their total net_balance from stocks (both copper & cassiterite)."""
    from cassiterite.models import CassiteriteStock
    
    q = (request.args.get('q') or '').strip()
    
    # Query copper stocks with net_balance > 0
    copper_suppliers = db.session.query(
        CopperStock.supplier.label('supplier'),
        func.coalesce(func.sum(CopperStock.net_balance), 0).label('total_net_balance')
    ).filter(
        CopperStock.is_deleted.is_(False),
        CopperStock.net_balance > 0,
    ).group_by(CopperStock.supplier).all()
    
    # Query cassiterite stocks with balance_to_pay > 0
    cass_suppliers = db.session.query(
        CassiteriteStock.supplier.label('supplier'),
        func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0).label('total_net_balance')
    ).filter(
        CassiteriteStock.is_deleted.is_(False),
        CassiteriteStock.balance_to_pay > 0,
    ).group_by(CassiteriteStock.supplier).all()
    
    # Merge suppliers and sum their balances
    supplier_map = {}
    for r in copper_suppliers:
        supplier_name = (r.supplier or '').strip()
        if supplier_name not in supplier_map:
            supplier_map[supplier_name] = 0.0
        supplier_map[supplier_name] += float(r.total_net_balance or 0.0)
    
    for r in cass_suppliers:
        supplier_name = (r.supplier or '').strip()
        if supplier_name not in supplier_map:
            supplier_map[supplier_name] = 0.0
        supplier_map[supplier_name] += float(r.total_net_balance or 0.0)
    
    # Filter by query if provided
    results = []
    for supplier_name, total_balance in sorted(supplier_map.items()):
        if q and q.lower() not in supplier_name.lower():
            continue
        if total_balance <= 0:
            continue
        results.append({
            'supplier': supplier_name,
            'total_balance': f"{total_balance:,.2f}",
            'total_balance_raw': float(total_balance),
        })
    
    return safe_jsonify(results)


@copper_bp.route('/record_supplier_settlement', methods=['GET', 'POST'])
@role_required('accountant', 'admin')
def record_supplier_settlement():
    """Record a supplier settlement payment for all their unpaid stocks (bypasses approvals).
    
    The user selects a supplier, sees their total stock balance, optionally overrides
    the amount, chooses currency, and creates a settlement that is immediately disbursed.
    """
    from flask_login import current_user
    from cassiterite.models import CassiteriteStock
    
    if request.method == 'POST':
        supplier_name = (request.form.get('supplier_name') or '').strip()
        if not supplier_name:
            flash('Please select a supplier.', 'danger')
            return redirect(url_for('copper.record_supplier_settlement'))
        
        # Get total stock balance for this supplier (across both minerals)
        copper_total = float(
            db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0))
            .filter(
                CopperStock.is_deleted.is_(False),
                CopperStock.supplier == supplier_name,
                CopperStock.net_balance > 0,
            ).scalar() or 0.0
        )
        
        cass_total = float(
            db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0))
            .filter(
                CassiteriteStock.is_deleted.is_(False),
                CassiteriteStock.supplier == supplier_name,
                CassiteriteStock.balance_to_pay > 0,
            ).scalar() or 0.0
        )
        
        total_stock_balance = copper_total + cass_total
        
        if total_stock_balance <= 0:
            flash(f'No unpaid stocks found for supplier "{supplier_name}".', 'warning')
            return redirect(url_for('copper.record_supplier_settlement'))
        
        # Get payment amount (default to full balance, but allow override)
        try:
            payment_amount = float(request.form.get('payment_amount') or total_stock_balance)
        except (TypeError, ValueError):
            payment_amount = total_stock_balance
        
        if payment_amount <= 0:
            flash('Payment amount must be greater than 0.', 'danger')
            return redirect(url_for('copper.record_supplier_settlement'))
        
        # Get currency and exchange rate
        currency = (request.form.get('currency') or 'RWF').upper()
        exchange_rate_input = request.form.get('exchange_rate') or None
        note = (request.form.get('note') or '').strip()
        
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(payment_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('copper.record_supplier_settlement'))
        
        # Create supplier if doesn't exist
        supplier_id = _get_or_create_supplier_id(supplier_name)
        
        # Create settlement payment (unlinked to specific stock, approved/disbursed immediately)
        try:
            payment = SupplierPayment(
                stock_id=None,  # Unlinked settlement
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                amount=amount_rwf,
                input_amount=payment_amount,
                currency=currency,
                exchange_rate=exchange_rate,
                amount_rwf=amount_rwf,
                method=request.form.get('method') or 'cash',
                reference=request.form.get('reference') or '',
                note=note + (f" [Settlement for {total_stock_balance:,.2f} RWF in stocks]" if payment_amount != total_stock_balance else " [Settlement for all stocks]"),
                payment_type='SETTLEMENT',
                is_advance=False,
                approval_status='APPROVED',  # Bypass boss approval
                disbursement_status='DISBURSED',  # Immediate disbursement
                approved_by_id=getattr(current_user, 'id', None),
                approved_at=datetime.utcnow(),
                disbursed_by_id=getattr(current_user, 'id', None),
                disbursed_at=datetime.utcnow(),
                created_by_id=getattr(current_user, 'id', None),
            )
            db.session.add(payment)
            db.session.commit()
            
            flash(f'Settlement of {amount_rwf:,.2f} RWF ({payment_amount:,.2f} {currency}) recorded for {supplier_name}. Payment appears in ledger immediately.', 'success')
            return redirect(url_for('copper.record_supplier_settlement'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating settlement: {str(e)}', 'danger')
            return redirect(url_for('copper.record_supplier_settlement'))
    
    # GET request - show the form
    return render_template('copper/record_supplier_settlement.html')


@copper_bp.route('/pay_worker', methods=['GET', 'POST'])
@role_required('accountant')
def pay_worker():
    """Record internal worker payments/expenses for copper."""
    from flask import current_app
    from flask_login import current_user
    from core.models import PaymentReview, PaymentReviewStatus, create_notification, User

    form = WorkerPaymentForm()

    if form.validate_on_submit():
        try:
            payload = {
                "worker_name": form.worker_name.data,
                "method": form.method.data,
                "reference": form.reference.data,
                "note": form.note.data,
            }
            review = PaymentReview(
                mineral_type='coltan',
                type='umukozi',
                customer=form.worker_name.data,
                amount=form.amount.data,
                currency='RWF',
                payment_id=None,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment='kwishyura umukozi',
                request_payload=json.dumps(payload),
            )
            db.session.add(review)
            db.session.commit()

            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='PAYMENT_EXECUTED',
                    message=f"Hasabwe kwemeza: Depense zimbere - {form.worker_name.data}, Amafaranga: {form.amount.data} RWF.",
                    related_type='depense zimbere',
                    related_id=review.id,
                )
            # Persist in-app notification before attempting email
            db.session.commit()

            from utils import send_brevo_email_async

            boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
            payment_details = (
                f"Umukozi: {form.worker_name.data}, Amafaranga: {form.amount.data} RWF, Uburyo: {form.method.data}, "
                f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
            )
            subject = "Saba Kwemezwa: Depense Zimbere"
            html_content = (
                "<p>Nyakubahwa Muyobozi,</p>"
                f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) "
                f"yasabye kwemeza depense zimbere zikurikira :</p>"
                f"<p>{payment_details}</p>"
                "<p>Musuzume kandi mwemeze.</p>"
                "<p>Murakoze,<br>Urumuli Smart System</p>"
            )
            try:
                send_brevo_email_async(subject, html_content, boss_email)
            except Exception:
                import logging
                logging.exception("Failed to send worker payment email")
                flash("Email notification failed; in-app notification saved.", "warning")

            flash(f"Payment request of {form.amount.data} RWF sent for boss approval ({form.worker_name.data}).", "success")
            return redirect(url_for('copper.pay_worker'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving payment: {e}", "danger")
            pending_reviews = PaymentReview.query.filter_by(
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_worker.html', form=form, pending_reviews=pending_reviews)

    recent_payments = WorkerPayment.query.filter(
        WorkerPayment.is_deleted.is_(False)
    ).order_by(WorkerPayment.paid_at.desc()).limit(15).all()
    pending_reviews = PaymentReview.query.filter_by(
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
    ).order_by(PaymentReview.created_at.desc()).limit(10).all()
    return render_template('copper/pay_worker.html', form=form, recent_payments=recent_payments, pending_reviews=pending_reviews)


@copper_bp.route('/supplier/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_supplier_payment(payment_id):
    from copper.forms import SupplierPaymentForm
    from copper.models import SupplierPayment
    from copper.models import CopperStock
    from core.models import PaymentReview, PaymentReviewStatus
    import json

    payment = SupplierPayment.query.get_or_404(payment_id)
    form = SupplierPaymentForm()

    # Use column-only query for choices
    stock_rows = db.session.query(CopperStock.id, CopperStock.voucher_no, CopperStock.supplier).order_by(CopperStock.date.desc()).all()
    form.stock_id.choices = [(r.id, f"{r.voucher_no} - {r.supplier}") for r in stock_rows]

    if form.validate_on_submit():
        # When editing an existing payment we must have a change reason.
        if not (form.change_reason.data and form.change_reason.data.strip()):
            flash('Change reason is required when editing a payment.', 'danger')
            return render_template('copper/edit_supplier_payment.html', form=form, payment=payment)

        try:
            stock = CopperStock.query.get(form.stock_id.data)
            from flask_login import current_user
            payload = {
                'action': 'edit_supplier_payment',
                'payment_id': payment.id,
                'stock_id': int(stock.id) if stock else None,
                'old_values': {
                    'stock_id': payment.stock_id,
                    'amount': float(payment.amount or 0),
                    'method': payment.method,
                    'reference': payment.reference,
                    'note': payment.note,
                },
                'new_values': {
                    'stock_id': int(stock.id) if stock else None,
                    'amount': float(form.amount.data or 0),
                    'method': form.method.data,
                    'reference': form.reference.data,
                    'note': form.note.data,
                },
                'change_reason': form.change_reason.data.strip(),
            }
            review = PaymentReview(
                mineral_type='copper',
                type='Utanga ibicuruzwa',
                customer=stock.supplier if stock else (payment.supplier_name or 'Unknown'),
                amount=float(form.amount.data or 0),
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=json.dumps(payload),
                boss_comment=f"Edit requested: {form.change_reason.data.strip()}",
            )
            db.session.add(review)
            # in-app notification and email to boss
            from core.models import create_notification, User
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='Guhindura ibyakozwe mbere',
                    message=f"Hasabwe gusuzuma: Impinduka kuri kwishyura utanga ibicuruzwa - {stock.supplier}, Amafaranga: {payment.amount} RWF. Impamvu: {form.change_reason.data.strip()}",
                    related_type='supplier_payment',
                    related_id=payment.id,
                )
            db.session.commit()

            # send email (best-effort)
            try:
                from utils import send_brevo_email_async
                boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
                subject = "Saba Kwemezwa: Impinduka kuri Kwishyura utanga ibicuruzwa (Coltan)"
                html_content = (
                    "<p>Nyakubahwa Muyobozi,</p>"
                    f"<p>Umucungamutungo {getattr(current_user,'username','Unknown')} "
                    f"yasabye ko musuzuma impinduka zikurikira kuri kwishyura utanga amabuye kuri Coltan:</p>"
                    f"<p>Umutanga: {stock.supplier}<br>Amafaranga (byahinduwe): {payment.amount} RWF<br>Impamvu: {form.change_reason.data.strip()}</p>"
                    "<p>Murakoze,<br>Mujye Muri system kwemeza iki gikorwa.<br>Urumuli Smart System</p>"
                )
                try:
                    send_brevo_email_async(subject, html_content, boss_email)
                except Exception:
                    import logging
                    logging.exception("Failed to send supplier edit email")
                    flash("Email notification failed; in-app notification saved.", "warning")
            except Exception:
                pass

            flash('Supplier payment edit request submitted for boss approval.', 'warning')
            return redirect(url_for('copper.pay_supplier'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating payment: {e}', 'danger')

    if not form.is_submitted():
        form.stock_id.data = payment.stock_id
        form.amount.data = payment.amount
        form.method.data = payment.method
        form.reference.data = payment.reference
        form.note.data = payment.note

    return render_template('copper/edit_supplier_payment.html', form=form, payment=payment)


@copper_bp.route('/supplier/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_supplier_payment(payment_id):
    from copper.models import SupplierPayment, CopperStock
    from core.models import PaymentReview

    payment = SupplierPayment.query.get_or_404(payment_id)
    stock = CopperStock.query.get(payment.stock_id)
    # Require a reason for deletion (submitted via hidden input)
    reason = request.form.get('change_reason', '')
    if not reason or not reason.strip():
        flash('Delete reason is required.', 'danger')
        return redirect(url_for('copper.pay_supplier'))

    try:
        # Create a PaymentReview so the boss can approve the deletion.
        from flask_login import current_user
        from core.models import create_notification, User
        # upsert pending review for delete-request
        from core.models import PaymentReviewStatus
        existing = PaymentReview.query.filter_by(
            payment_id=payment.id,
            status=PaymentReviewStatus.PENDING_REVIEW.value,
        ).first()
        if existing:
            existing.mineral_type = 'coltan'
            existing.type = 'Utanga amabuye'
            existing.customer = stock.supplier
            existing.amount = payment.amount
            existing.currency = 'RWF'
            existing.created_by_id = getattr(current_user, 'id', None)
            existing.boss_comment = (f"Delete requested: {reason.strip()}")
        else:
            review = PaymentReview(
                mineral_type='coltan',
                type='Utanga amabuye',
                customer=stock.supplier,
                amount=payment.amount,
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment=(f"Delete requested: {reason.strip()}"),
            )
            db.session.add(review)
        boss_user = User.query.filter_by(role='boss').first()
        if boss_user:
            create_notification(
                user_id=boss_user.id,
                type_='PAYMENT_DELETE_REQUEST',
                message=f"Hasabwe gusuzuma: Gusiba kwishyura utanga amabuye (Coltan) - {stock.supplier}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {reason.strip()}",
                related_type='supplier_payment',
                related_id=payment.id,
            )
        db.session.commit()
        flash('Delete request submitted for boss review; payment was not deleted until approval.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error submitting delete request: {e}', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting payment: {e}', 'danger')

    return redirect(url_for('copper.pay_supplier'))


@copper_bp.route('/worker/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_worker_payment(payment_id):
    from copper.forms import WorkerPaymentForm
    from copper.models import WorkerPayment
    from core.models import PaymentReview, PaymentReviewStatus
    import json

    payment = WorkerPayment.query.get_or_404(payment_id)
    form = WorkerPaymentForm()

    if form.validate_on_submit():
        # Must provide a reason for edits
        if not (form.change_reason.data and form.change_reason.data.strip()):
            flash('Change reason is required when editing a payment.', 'danger')
            return render_template('copper/edit_worker_payment.html', form=form, payment=payment)

        try:
            # Create approval request WITHOUT executing the edit
            from flask_login import current_user
            from core.models import create_notification, User
            
            # Store the proposed changes in the payload
            payload = {
                'action': 'edit_worker_payment',
                'payment_id': payment_id,
                'old_values': {
                    'worker_name': payment.worker_name,
                    'amount': float(payment.amount or 0),
                    'method': payment.method,
                    'reference': payment.reference,
                    'note': payment.note,
                },
                'new_values': {
                    'worker_name': form.worker_name.data,
                    'amount': float(form.amount.data or 0),
                    'method': form.method.data,
                    'reference': form.reference.data,
                    'note': form.note.data,
                },
                'change_reason': form.change_reason.data.strip(),
            }
            
            review = PaymentReview(
                mineral_type='copper',
                type='expense_edit',
                customer=f"{payment.worker_name} (current) -> {form.worker_name.data} (proposed)",
                amount=float(form.amount.data or 0),
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
                request_payload=json.dumps(payload),
                boss_comment=f"Expense edit requested: {form.change_reason.data.strip()}"
            )
            db.session.add(review)
            
            # Notify all bosses
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="expense_edit_approval",
                    message=f"Accountant {getattr(current_user, 'username', 'unknown')} requested approval to edit expense for {payment.worker_name} (amount: {payment.amount} RWF -> {form.amount.data} RWF).",
                    related_type="payment_review",
                    related_id=review.id
                )
            
            db.session.commit()
            flash('Expense edit request submitted for boss approval. The edit will be executed after approval.', 'warning')
            return redirect(url_for('copper.pay_worker'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting edit request: {e}', 'danger')

    if not form.is_submitted():
        form.worker_name.data = payment.worker_name
        form.amount.data = payment.amount
        form.method.data = payment.method
        form.reference.data = payment.reference
        form.note.data = payment.note

    return render_template('copper/edit_worker_payment.html', form=form, payment=payment)


@copper_bp.route('/worker/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_worker_payment(payment_id):
    from copper.models import WorkerPayment
    from core.models import PaymentReview

    payment = WorkerPayment.query.get_or_404(payment_id)
    reason = request.form.get('change_reason', '')
    if not reason or not reason.strip():
        flash('Delete reason is required.', 'danger')
        return redirect(url_for('copper.pay_worker'))

    try:
        from flask_login import current_user
        from core.models import create_notification, User
        # upsert pending review for worker delete request
        from core.models import PaymentReviewStatus
        existing = PaymentReview.query.filter_by(
            payment_id=payment.id,
            status=PaymentReviewStatus.PENDING_REVIEW.value,
        ).first()
        if existing:
            existing.mineral_type = None
            existing.type = 'Umukozi'
            existing.customer = payment.worker_name
            existing.amount = payment.amount
            existing.currency = 'RWF'
            existing.created_by_id = getattr(current_user, 'id', None)
            existing.boss_comment = (f"Delete requested: {reason.strip()}")
        else:
            review = PaymentReview(
                mineral_type=None,
                type='Umukozi',
                customer=payment.worker_name,
                amount=payment.amount,
                currency='RWF',
                payment_id=payment.id,
                created_by_id=getattr(current_user, 'id', None),
                boss_comment=(f"Delete requested: {reason.strip()}"),
            )
            db.session.add(review)
        boss_user = User.query.filter_by(role='boss').first()
        if boss_user:
            create_notification(
                user_id=boss_user.id,
                type_='PAYMENT_DELETE_REQUEST',
                message=f"Hasabwe gusuzuma: Gusiba kwishyura umukozi - {payment.worker_name}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {reason.strip()}",
                related_type='worker_payment',
                related_id=payment.id,
            )
        db.session.commit()
        flash('Delete request submitted for boss review; payment was not deleted until approval.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error submitting delete request: {e}', 'danger')

    return redirect(url_for('copper.pay_worker'))
