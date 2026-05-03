"""
Payment Routes
Handles supplier and worker payment recording for copper.
"""
import json

from flask import render_template, redirect, url_for, flash, abort

from config import db
from copper.models import CopperStock, SupplierPayment, WorkerPayment, CopperSupplier, CopperAdvanceAllocation
from copper.forms import SupplierPaymentForm, WorkerPaymentForm
from copper import copper_bp
from core.auth import role_required
from flask import request
from sqlalchemy import func, or_


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
    # Step 1: Get all stocks for this supplier
    stocks = CopperStock.query.filter(
        CopperStock.supplier == supplier_name,
        CopperStock.is_deleted.is_(False)
    ).all()
    
    if not stocks:
        return 0.0
    
    stock_ids = [s.id for s in stocks]
    
    # Step 2: Calculate total allocated advances from copper_advance_allocation
    allocations = db.session.query(
        CopperAdvanceAllocation.stock_id,
        func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocated')
    ).filter(
        CopperAdvanceAllocation.stock_id.in_(stock_ids)
    ).group_by(CopperAdvanceAllocation.stock_id).all()
    
    allocation_map = {a.stock_id: float(a.allocated) for a in allocations}
    
    # Step 3: Calculate total owed (net balances after subtracting allocations)
    total_owed = sum(
        max((s.net_balance or 0.0) - allocation_map.get(s.id, 0.0), 0.0)
        for s in stocks
    )
    
    # Step 4: Calculate total paid (settlements only, NOT advances)
    total_paid = db.session.query(
        func.coalesce(func.sum(SupplierPayment.amount_rwf), 0)
    ).filter(
        SupplierPayment.supplier_name == supplier_name,
        SupplierPayment.is_advance.is_(False),
        SupplierPayment.is_deleted.is_(False)
    ).scalar() or 0.0
    
    # Step 5: Calculate remaining
    return max(total_owed - total_paid, 0.0)


@copper_bp.route('/supplier/payment/<int:payment_id>/receipt')
@role_required('accountant')
def supplier_receipt(payment_id):
    """
    Shows a printable receipt for a copper supplier payment.
    
    For ADVANCE payments (no stock):
      - Shows amount paid as advance
      - Shows remaining from this advance
      
    For SETTLEMENT payments (linked to stock):
      - Shows payment against stock balance
      - Shows remaining on that stock after payment
    """
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
        # Get total allocated from this advance to any stocks
        allocated = db.session.query(
            func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0)
        ).filter(
            CopperAdvanceAllocation.supplier_payment_id == payment.id
        ).scalar() or 0.0
        
        remaining_before = 0.0  # Advance doesn't have "before" - it's new money
        remaining_after = float(payment.advance_remaining or 0.0)
        applied_to_stock = float(allocated)
    
    # For SETTLEMENT payments (linked to stock)
    else:
        # Total paid for this stock (all payments, including this one)
        total_paid = db.session.query(
            func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0)
        ).filter(SupplierPayment.stock_id == stock.id).scalar() if stock else 0.0

        # Remaining after this payment has been applied
        remaining_after = ((stock.net_balance or 0.0) - total_paid) if stock else 0.0

        # Remaining before this payment (useful to show previous balance)
        remaining_before = remaining_after + (payment.amount_rwf or payment.amount or 0.0)
        applied_to_stock = 0.0

    template_name = 'receipts/advance_payment_form.html' if bool(payment.is_advance or not payment.stock_id) else 'receipts/settlement_payment_form.html'

    return render_template(
        template_name,
        payment=payment,
        supplier_name=supplier_name,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
        applied_to_stock=applied_to_stock,
        is_advance=bool(payment.is_advance or not payment.stock_id),
    )


@copper_bp.route('/worker/payment/<int:payment_id>/receipt')
@role_required('accountant')
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
    if request.method == 'GET':
        requested_kind = (request.args.get('payment_kind') or '').strip().lower()
        if requested_kind == 'advance':
            return redirect(url_for('copper.pay_supplier_advance'))
        form.payment_kind.data = 'settlement'

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
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            pending_reviews = PaymentReview.query.filter_by(
                created_by_id=getattr(current_user, 'id', None),
                status=PaymentReviewStatus.PENDING_REVIEW.value,
            ).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews)
        payment_kind = 'settlement'

        try:
            payment_supplier = None
            stock = None

            stock = CopperStock.query.get_or_404(form.stock_id.data)
            payment_supplier = stock.supplier
            supplier_id = _get_or_create_supplier_id(payment_supplier)
            if amount_rwf > stock.remaining_to_pay():
                flash(f"Payment exceeds remaining balance ({stock.remaining_to_pay()} RWF).", "danger")
                pending_reviews = PaymentReview.query.filter_by(
                    created_by_id=getattr(current_user, 'id', None),
                    status=PaymentReviewStatus.PENDING_REVIEW.value,
                ).order_by(PaymentReview.created_at.desc()).limit(10).all()
                return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews)
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
            return render_template('copper/pay_supplier.html', form=form, pending_reviews=pending_reviews)

    # GET or not-submitted
    # Build supplier summaries using DB-level aggregation and pagination
    supplier_query = (request.args.get('supplier') or '').strip()
    per_page = 15
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    # Subquery: total net balance per supplier (only active stocks)
    stock_net_subq = (
        db.session.query(
            CopperStock.supplier.label('supplier'),
            func.coalesce(func.sum(CopperStock.net_balance), 0).label('total_net')
        )
        .filter(CopperStock.is_deleted.is_(False), CopperStock.net_balance > 0)
        .group_by(CopperStock.supplier)
        .subquery()
    )

    # Subquery: total allocations applied per supplier (join allocations -> stock)
    alloc_subq = (
        db.session.query(
            CopperStock.supplier.label('supplier'),
            func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('total_alloc')
        )
        .join(CopperStock, CopperStock.id == CopperAdvanceAllocation.stock_id)
        .filter(CopperStock.is_deleted.is_(False))
        .group_by(CopperStock.supplier)
        .subquery()
    )

    # Subquery: payments aggregated by supplier (use stock.supplier when linked)
    payments_subq = (
        db.session.query(
            func.coalesce(CopperStock.supplier, SupplierPayment.supplier_name).label('supplier'),
            func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0).label('total_paid'),
            func.max(SupplierPayment.paid_at).label('latest_paid_at')
        )
        .outerjoin(CopperStock, CopperStock.id == SupplierPayment.stock_id)
        .filter(SupplierPayment.is_deleted.is_(False))
        .group_by(func.coalesce(CopperStock.supplier, SupplierPayment.supplier_name))
        .subquery()
    )

    # Subquery: vouchers per supplier (string_agg)
    vouchers_subq = (
        db.session.query(
            CopperStock.supplier.label('supplier'),
            func.coalesce(func.string_agg(func.distinct(CopperStock.voucher_no), ', '), '').label('vouchers')
        )
        .filter(CopperStock.is_deleted.is_(False))
        .group_by(CopperStock.supplier)
        .subquery()
    )

    # Join the subqueries to produce supplier-level rows
    base_q = (
        db.session.query(
            stock_net_subq.c.supplier.label('supplier'),
            (stock_net_subq.c.total_net - func.coalesce(alloc_subq.c.total_alloc, 0)).label('net_balance'),
            func.coalesce(payments_subq.c.total_paid, 0).label('total_paid'),
            payments_subq.c.latest_paid_at.label('latest_paid_at'),
            func.coalesce(vouchers_subq.c.vouchers, '').label('vouchers')
        )
        .outerjoin(alloc_subq, alloc_subq.c.supplier == stock_net_subq.c.supplier)
        .outerjoin(payments_subq, payments_subq.c.supplier == stock_net_subq.c.supplier)
        .outerjoin(vouchers_subq, vouchers_subq.c.supplier == stock_net_subq.c.supplier)
    )

    if supplier_query:
        base_q = base_q.filter(stock_net_subq.c.supplier.ilike(f"%{supplier_query}%"))

    # Total count for pagination
    count_q = db.session.query(func.count()).select_from(base_q.subquery())
    total_suppliers = int(count_q.scalar() or 0)
    total_pages = (total_suppliers + per_page - 1) // per_page if total_suppliers else 1
    if page > total_pages:
        page = total_pages
    offset_val = (page - 1) * per_page

    rows = base_q.order_by(
        # Put suppliers with recent payments first, nulls last
        (payments_subq.c.latest_paid_at.is_(None)).asc(),
        payments_subq.c.latest_paid_at.desc(),
        stock_net_subq.c.supplier.asc()
    ).limit(per_page).offset(offset_val).all()
    # Build a map of recent payments for suppliers on this page so templates
    # can render per-supplier View/Print receipt links (restore regression).
    supplier_summaries = []
    recent_suppliers = []

    page_supplier_names = [((r.supplier or '').strip()) for r in rows if (r.supplier or '').strip()]
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
                'amount': float(payment.amount_rwf or payment.amount or 0),
            })
        # limit to most recent 5 per supplier
        for k in list(payments_map.keys()):
            payments_map[k] = payments_map[k][:5]

    for r in rows:
        supplier_name = (r.supplier or '').strip()
        net_balance = float(r.net_balance or 0.0)
        total_paid = float(r.total_paid or 0.0)
        remaining = float(net_balance - total_paid)
        latest_paid_at = getattr(r, 'latest_paid_at', None)
        supplier_summaries.append({
            'supplier': supplier_name,
            'vouchers': r.vouchers or '',
            'net_balance': net_balance,
            'total_paid': total_paid,
            'remaining': remaining,
            'payments': payments_map.get(supplier_name, []),
            'latest_paid_at': latest_paid_at,
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
    for row in stock_rows:
        key = (row.supplier or '').strip()
        if not key:
            continue
        summary = supplier_summary_map.setdefault(key, {'supplier': key, 'owed': 0.0, 'paid': 0.0, 'remaining': 0.0})
        # CORRECT: Subtract allocations from net_balance
        net_owed = max((row.net_balance or 0.0) - allocation_map.get(row.id, 0.0), 0.0)
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
        summary['remaining'] = max(summary['owed'] - summary['paid'], 0.0)

    supplier_names = sorted({(r.supplier or '').strip() for r in stock_rows if (r.supplier or '').strip()})
    form.existing_supplier.choices = [
        ('', 'Select existing supplier'),
        *[(s, f"{s} - Owed: {supplier_summary_map.get(s, {}).get('remaining', 0.0):,.2f} RWF") for s in supplier_names],
    ]
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
        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
        except ValueError as exc:
            flash(str(exc), 'danger')
            pending_reviews = PaymentReview.query.filter_by(created_by_id=getattr(current_user, 'id', None), status=PaymentReviewStatus.PENDING_REVIEW.value).order_by(PaymentReview.created_at.desc()).limit(10).all()
            return render_template('copper/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)
        supplier = (form.new_supplier.data or form.existing_supplier.data or '').strip()
        if not supplier:
            flash('Please select an existing supplier or enter a new supplier name for advance payment.', 'danger')
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
                    f'<p>Supplier: {supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}), Uburyo: {form.method.data}, Reference: {form.reference.data}, Impamvu: {form.note.data}</p>'
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
                    message=f"Hasabwe kwemeza: Kwishyura umukozi  - {form.worker_name.data}, Amafaranga: {form.amount.data} RWF.",
                    related_type='kwishyura umukozi',
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
            subject = "Saba Kwemezwa: Kwishyura Umukozi "
            html_content = (
                "<p>Nyakubahwa Muyobozi,</p>"
                f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) "
                f"yasabye kwemeza ubwishyu bukurikira :</p>"
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

    recent_payments = WorkerPayment.query.order_by(WorkerPayment.paid_at.desc()).limit(15).all()
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
    from core.models import PaymentReview

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
            payment.stock_id = stock.id
            payment.amount = form.amount.data
            payment.method = form.method.data
            payment.reference = form.reference.data
            payment.note = form.note.data
            db.session.add(payment)
            db.session.commit()
            db.session.flush()  # ensure payment.id is populated for the review record  
            # Create a new PaymentReview for the boss to review this change.
            # We keep existing review rows untouched (they represent what was
            # previously recorded) and create a fresh PENDING_REVIEW entry
            # that contains the new values and the accountant's reason.
            from flask_login import current_user
            # upsert pending review for this edited supplier payment (include change reason)
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
                existing.boss_comment = (f"Edit requested: {form.change_reason.data.strip()}")
            else:
                review = PaymentReview(
                    mineral_type='coltan',
                    type='Utanga amabuye',
                    customer=stock.supplier,
                    amount=payment.amount,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                    boss_comment=(f"Edit requested: {form.change_reason.data.strip()}"),
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

            flash('Supplier payment updated; boss has been notified to review the change.', 'success')
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
    from core.models import PaymentReview

    payment = WorkerPayment.query.get_or_404(payment_id)
    form = WorkerPaymentForm()

    if form.validate_on_submit():
        # Must provide a reason for edits
        if not (form.change_reason.data and form.change_reason.data.strip()):
            flash('Change reason is required when editing a payment.', 'danger')
            return render_template('copper/edit_worker_payment.html', form=form, payment=payment)

        try:
            payment.worker_name = form.worker_name.data
            payment.amount = form.amount.data
            payment.method = form.method.data
            payment.reference = form.reference.data
            payment.note = form.note.data
            db.session.add(payment)
            db.session.commit()

            # Create a PENDING review for the boss to inspect this edit
            from flask_login import current_user
            from core.models import create_notification, User
            # upsert pending review for edited worker payment
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
                existing.boss_comment = (f"Edit requested: {form.change_reason.data.strip()}")
            else:
                review = PaymentReview(
                    mineral_type=None,
                    type='Umukozi',
                    customer=payment.worker_name,
                    amount=payment.amount,
                    currency='RWF',
                    payment_id=payment.id,
                    created_by_id=getattr(current_user, 'id', None),
                    boss_comment=(f"Edit requested: {form.change_reason.data.strip()}"),
                )
                db.session.add(review)
            boss_user = User.query.filter_by(role='boss').first()
            if boss_user:
                create_notification(
                    user_id=boss_user.id,
                    type_='PAYMENT_EDIT_REQUEST',
                    message=f"Hasabwe gusuzuma: Impinduka kuri kwishyura umukozi - {payment.worker_name}, Amafaranga: {payment.amount} RWF. Icyitonderwa: {form.change_reason.data.strip()}",
                    related_type='worker_payment',
                    related_id=payment.id,
                )
            db.session.commit()
            flash('Worker payment updated; boss has been notified to review the change.', 'success')
            return redirect(url_for('copper.pay_worker'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating payment: {e}', 'danger')

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
