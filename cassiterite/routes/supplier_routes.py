import json
from flask import render_template, redirect, abort, request
from flask_login import current_user
from datetime import datetime

from cassiterite.models.workers_payment import CassiteriteWorkerPayment
from cassiterite.forms import CassiteriteWorkerPaymentForm, CassiteriteSupplierPaymentForm
from cassiterite.models.payment import CassiteriteSupplierPayment, CassiteriteSupplier
from cassiterite.routes import cassiterite_bp
from core.auth import role_required
from flask import url_for, flash
from config import db
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
	supplier = CassiteriteSupplier.query.filter(func.lower(CassiteriteSupplier.name) == clean.lower()).first()
	if supplier:
		return supplier.id
	supplier = CassiteriteSupplier(name=clean)
	db.session.add(supplier)
	db.session.flush()
	return supplier.id

@cassiterite_bp.route("/manage_expenses", methods=["GET", "POST"], endpoint="manage_expenses")
@cassiterite_bp.route("/pay_worker", methods=["GET", "POST"], endpoint="pay_worker")
@role_required("accountant")
def pay_worker():
	"""Create internal expense requests; boss approval executes the payment."""
	form = CassiteriteWorkerPaymentForm()

	if form.validate_on_submit():
		try:
			# --- Create PaymentReview request; actual payment executes on boss approval ---
			from core.models import PaymentReview
			payload = {
				"worker_name": form.worker_name.data,
				"method": form.method.data,
				"reference": form.reference.data,
				"note": form.note.data,
				"accountant_name": getattr(current_user, 'username', None),
				"cashier_name": (form.cashier_name.data or '').strip(),
			}
			review = PaymentReview(
				mineral_type='cassiterite',
				type='Umukozi',
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

			# --- IN-APP NOTIFICATION TO BOSS ---
			from core.models import create_notification, User
			boss_user = User.query.filter_by(role='boss').first()
			if boss_user:
				create_notification(
					user_id=boss_user.id,
					type_='depense zimbere',
					message=f"Hasabwe kwemeza: Depense zimbere - {form.worker_name.data}, Amafaranga: {form.amount.data} RWF.",
					related_type='depense zimbere',
					related_id=review.id
				)

			# Persist in-app notification before attempting email
			db.session.commit()

			# --- EMAIL NOTIFICATION TO BOSS (Brevo) ---
			from flask import current_app
			from utils import send_brevo_email_async
			boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
			payment_details = (
				f"Umukozi: {form.worker_name.data}, Amafaranga: {form.amount.data} RWF, Uburyo: {form.method.data}, "
				f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
			)
			subject = "Gusaba Kwemeza Igikorwa: Depense Zimbere"
			html_content = (
				"<p>Nyakubahwa Muyobozi,</p>"
				f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) yasabye kwemeza depense zimbere zikurikira :</p>"
				f"<p>{payment_details}</p>"
				"<p>Musuzume kandi mwemeze.</p>"
				"<p>Mujye Muri Sisiteme kwemeza iki gikorwa.<br>Murakoze,<br>Urumuli Smart System</p>"
			)
			try:
				send_brevo_email_async(subject, html_content, boss_email)
			except Exception as e:
				import logging
				logging.exception("Failed to enqueue worker payment email notification via Brevo")
				flash("Email notification failed; in-app notification saved.", "warning")

			flash(f"Expense request of {form.amount.data} RWF sent for boss approval ({form.worker_name.data}).", "success")
			return redirect(url_for('cassiterite.manage_expenses'))
		except Exception as e:
			db.session.rollback()
			flash(f"Error saving payment: {e}", "danger")

	# Optionally, show recent worker payments
	from core.models import PaymentReview, PaymentReviewStatus
	try:
		recent_payments = CassiteriteWorkerPayment.query.filter(
			CassiteriteWorkerPayment.is_deleted.is_(False)
		).order_by(CassiteriteWorkerPayment.paid_at.desc()).limit(10).all()
	except Exception:
		# Backward compatibility if DB has not been upgraded yet.
		recent_payments = CassiteriteWorkerPayment.query.order_by(CassiteriteWorkerPayment.paid_at.desc()).limit(10).all()
	pending_reviews = PaymentReview.query.filter_by(
		created_by_id=getattr(current_user, 'id', None),
		status=PaymentReviewStatus.PENDING_REVIEW.value,
	).order_by(PaymentReview.created_at.desc()).limit(10).all()
	return render_template('cassiterite/pay_worker.html', form=form, recent_payments=recent_payments, pending_reviews=pending_reviews)
"""Cassiterite supplier-related routes.

Provides endpoints to record supplier payments and view supplier ledgers
for cassiterite stocks. Mirrors the copper supplier payment workflow.
"""

from flask import render_template, redirect, url_for, flash, request

from config import db
from . import cassiterite_bp
from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment
from core.auth import role_required


@cassiterite_bp.route("/pay_supplier", methods=["GET", "POST"])
@role_required("accountant")
def pay_supplier():
	"""Record supplier payments for cassiterite stocks."""
	from cassiterite.forms import CassiteriteSupplierPaymentForm
	from flask_login import current_user
	from core.models import PaymentReview, PaymentReviewStatus, create_notification, User
	from utils import send_brevo_email_async

	form = CassiteriteSupplierPaymentForm()
	selected_stock_label = ''

	# Defaults so template rendering never crashes on GET or failed POST.
	supplier_summaries = []
	recent_suppliers = []
	suppliers_pagination = {
		'page': 1,
		'per_page': 15,
		'total': 0,
		'pages': 1,
		'has_prev': False,
		'has_next': False,
		'prev_num': 0,
		'next_num': 0,
		'query': '',
	}
	supplier_query = (request.args.get('supplier') or '').strip()

	if request.method == 'GET':
		requested_kind = (request.args.get('payment_kind') or '').strip().lower()
		if requested_kind == 'advance':
			return redirect(url_for('cassiterite.pay_supplier_advance'))
		form.payment_kind.data = 'settlement'

	if request.method == 'POST':
		try:
			selected_stock_id = int(request.form.get('stock_id') or 0)
		except (TypeError, ValueError):
			selected_stock_id = 0
		if selected_stock_id:
			selected_stock = CassiteriteStock.query.get(selected_stock_id)
			if selected_stock:
				selected_stock_label = f"{selected_stock.voucher_no} - {selected_stock.supplier}"

	# Populate stock choices with stocks that still have balance to pay.
	# Only select needed columns to avoid loading full model objects.
	stock_rows = (
		db.session.query(
			CassiteriteStock.id,
			CassiteriteStock.voucher_no,
			CassiteriteStock.supplier,
			CassiteriteStock.balance_to_pay,
		)
		.filter(CassiteriteStock.balance_to_pay > 0, CassiteriteStock.is_deleted.is_(False))
		.order_by(CassiteriteStock.date.desc())
		.all()
	)
	form.stock_id.choices = [
		(
			row.id,
			f"{row.voucher_no} - {row.supplier} - Remaining: {float(row.balance_to_pay or 0.0):,.2f} RWF",
		)
		for row in stock_rows
	]
	supplier_names = sorted({(row.supplier or '').strip() for row in stock_rows if (row.supplier or '').strip()})
	form.existing_supplier.choices = [('', 'Select existing supplier')] + [(s, s) for s in supplier_names]

	if form.validate_on_submit():
		input_amount = float(form.amount.data or 0)
		currency = (form.currency.data or 'RWF').upper()
		exchange_rate_input = form.exchange_rate.data
		requested_paid_at = form.paid_at.data or datetime.utcnow()
		try:
			amount_rwf, exchange_rate = _normalize_amount_to_rwf(input_amount, currency, exchange_rate_input)
		except ValueError as exc:
			flash(str(exc), 'danger')
			return redirect(url_for("cassiterite.pay_supplier"))
		payment_kind = 'settlement'

		try:
			payment_supplier = None
			stock = None

			if not form.stock_id.data:
				flash('Please select a supplier obligation from the suggestions.', 'danger')
				return render_template('cassiterite/pay_supplier.html', form=form, selected_stock_label=selected_stock_label, supplier_summaries=supplier_summaries, pending_reviews=pending_reviews, recent_settlements=recent_settlements, recent_suppliers=recent_suppliers, suppliers_pagination=suppliers_pagination, supplier_query=supplier_query)

			stock = CassiteriteStock.query.get_or_404(form.stock_id.data)
			payment_supplier = stock.supplier
			supplier_id = _get_or_create_supplier_id(payment_supplier)
			stock_remaining = float(stock.remaining_to_pay() or 0.0)
			if amount_rwf > stock_remaining:
				flash(
					f"Payment exceeds remaining balance ({stock_remaining:,.2f} RWF).",
					"danger",
				)
				return render_template('cassiterite/pay_supplier.html', form=form, selected_stock_label=selected_stock_label, supplier_summaries=supplier_summaries, pending_reviews=pending_reviews, recent_settlements=recent_settlements, recent_suppliers=recent_suppliers, suppliers_pagination=suppliers_pagination, supplier_query=supplier_query)

			# --- create PaymentReview request; actual payment executes on boss approval ---
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
				mineral_type='cassiterite',
				type='Utanga ibicuruzwa',
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

			# --- IN-APP NOTIFICATION TO BOSS ---
			from core.models import create_notification, User
			boss_user = User.query.filter_by(role='boss').first()
			if boss_user:
				create_notification(
					user_id=boss_user.id,
					type_='Kwishyura utanga ibicuruzwa',
					message=f"Hasabwe kwemeza: Kwishyura utanga ibicuruzwa kuri Gasegereti - {payment_supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}).",
					related_type='Kwishyura utanga ibicuruzwa(gasegereti)',
					related_id=review.id
				)

			# Persist in-app notification before attempting email
			db.session.commit()

			# --- EMAIL NOTIFICATION TO BOSS (Brevo) ---
			boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
			payment_details = (
				f"Utanga amabuye: {payment_supplier}, Amafaranga: {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}), Uburyo: {form.method.data}, "
				f"Reference: {form.reference.data}, Impamvu: {form.note.data}"
			)
			subject = "Gusaba kwemeza igikorwa: Kwishyura utanga Amabuye (Gasegereti)"
			html_content = (
				"<p>Nyakubahwa Muyobozi,</p>"
				f"<p>Umucungamutungo {getattr(current_user, 'username', 'Unknown')} ({getattr(current_user, 'email', 'Unknown')}) yasabye kwemeza ubwishyu bukurikira kuri Gasegereti:</p>"
				f"<p>{payment_details}</p>"
				"<p>Nyamuneka musuzume kandi mwemeze. Mujye Muri Sisiteme kwemeza iki gikorwa</p>"
				"<p>Murakoze,<br>Urumuli Smart System</p>"
			)
			try:
				send_brevo_email_async(subject, html_content, boss_email)
			except Exception as e:
				import logging
				logging.exception("Failed to enqueue cassiterite supplier payment email notification via Brevo")
				flash("Email notification failed; in-app notification saved.", "warning")

			flash(
				f"Payment request of {amount_rwf:,.2f} RWF ({input_amount:,.2f} {currency}) sent for boss approval ({payment_supplier}).",
				"success",
			)
			return redirect(url_for("cassiterite.pay_supplier"))
		except Exception as e:  # pragma: no cover - defensive
			db.session.rollback()
			flash(f"Error saving payment: {e}", "danger")

	# Build supplier summaries using DB-level aggregation and pagination
	per_page = 15
	try:
		page = int(request.args.get('page', 1))
	except (TypeError, ValueError):
		page = 1
	if page < 1:
		page = 1

	from core.models import UnifiedSupplierAdvance

	# Subquery: total net balance (owed) per supplier from stocks
	stock_net_subq = (
		db.session.query(
			CassiteriteStock.supplier.label('supplier'),
			func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0).label('total_net')
		)
		.filter(CassiteriteStock.is_deleted.is_(False))
		.group_by(CassiteriteStock.supplier)
		.subquery()
	)

	# Subquery: suppliers that exist only via advance imports or standalone payments.
	advance_supplier_subq = (
		db.session.query(
			UnifiedSupplierAdvance.supplier_name.label('supplier')
		)
		.filter(
			UnifiedSupplierAdvance.is_deleted.is_(False),
			UnifiedSupplierAdvance.supplier_name.isnot(None),
			func.trim(UnifiedSupplierAdvance.supplier_name) != '',
		)
		.group_by(UnifiedSupplierAdvance.supplier_name)
		.subquery()
	)

	standalone_payment_supplier_subq = (
		db.session.query(
			CassiteriteSupplierPayment.supplier_name.label('supplier')
		)
		.filter(
			CassiteriteSupplierPayment.is_deleted.is_(False),
			CassiteriteSupplierPayment.stock_id.is_(None),
			CassiteriteSupplierPayment.supplier_name.isnot(None),
			func.trim(CassiteriteSupplierPayment.supplier_name) != '',
		)
		.group_by(CassiteriteSupplierPayment.supplier_name)
		.subquery()
	)

	# Master supplier list for the table: stock obligations + advance-only suppliers.
	supplier_name_union = (
		db.session.query(stock_net_subq.c.supplier.label('supplier'))
		.union(
			db.session.query(advance_supplier_subq.c.supplier.label('supplier')),
			db.session.query(standalone_payment_supplier_subq.c.supplier.label('supplier')),
		)
		.subquery()
	)

	# Subquery: payments aggregated by supplier (map stock payments to stock.supplier)
	payments_subq = (
		db.session.query(
			func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name).label('supplier'),
			func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0).label('total_paid'),
			func.max(CassiteriteSupplierPayment.paid_at).label('latest_paid_at')
		)
		.outerjoin(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
		.filter(CassiteriteSupplierPayment.is_deleted.is_(False))
		.group_by(func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name))
		.subquery()
	)

	# Subquery: vouchers per supplier
	vouchers_subq = (
		db.session.query(
			CassiteriteStock.supplier.label('supplier'),
			func.coalesce(func.string_agg(func.distinct(CassiteriteStock.voucher_no), ', '), '').label('vouchers')
		)
		.filter(CassiteriteStock.is_deleted.is_(False))
		.group_by(CassiteriteStock.supplier)
		.subquery()
	)

	base_q = (
		db.session.query(
			supplier_name_union.c.supplier.label('supplier'),
			stock_net_subq.c.total_net.label('net_balance'),
			func.coalesce(payments_subq.c.total_paid, 0).label('total_paid'),
			payments_subq.c.latest_paid_at.label('latest_paid_at'),
			func.coalesce(vouchers_subq.c.vouchers, '').label('vouchers')
		)
		.outerjoin(stock_net_subq, stock_net_subq.c.supplier == supplier_name_union.c.supplier)
		.outerjoin(payments_subq, payments_subq.c.supplier == supplier_name_union.c.supplier)
		.outerjoin(vouchers_subq, vouchers_subq.c.supplier == supplier_name_union.c.supplier)
	)

	if supplier_query:
		base_q = base_q.filter(supplier_name_union.c.supplier.ilike(f"%{supplier_query}%"))

	# Count and paginate
	count_q = db.session.query(func.count()).select_from(base_q.subquery())
	total_suppliers = int(count_q.scalar() or 0)
	total_pages = (total_suppliers + per_page - 1) // per_page if total_suppliers else 1
	if page > total_pages:
		page = total_pages
	offset_val = (page - 1) * per_page

	rows = base_q.order_by(
		(payments_subq.c.latest_paid_at.is_(None)).asc(),
		payments_subq.c.latest_paid_at.desc(),
		stock_net_subq.c.supplier.asc()
	).limit(per_page).offset(offset_val).all()

	# Build recent payments map for suppliers on this page so templates can render
	# per-supplier receipt links if needed (restore regression where they disappeared).
	page_supplier_names = [((r.supplier or '').strip()) for r in rows if (r.supplier or '').strip()]
	payments_map = {}
	remaining_map = calculate_consolidated_supplier_remaining_balances(page_supplier_names)
	if page_supplier_names:
		payment_rows = (
			db.session.query(CassiteriteSupplierPayment, CassiteriteStock.supplier.label('stock_supplier'))
			.outerjoin(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
			.filter(CassiteriteSupplierPayment.is_deleted.is_(False))
			.filter(func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name).in_(page_supplier_names))
			.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
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
		for k in list(payments_map.keys()):
			payments_map[k] = payments_map[k][:5]

	for r in rows:
		supplier_name = (r.supplier or '').strip()
		net_balance = float(r.net_balance or 0.0)
		total_paid = float(r.total_paid or 0.0)
		remaining = float(remaining_map.get(' '.join(supplier_name.lower().split()), 0.0) if supplier_name else 0.0)
		latest_paid_at = getattr(r, 'latest_paid_at', None)
		supplier_summaries.append({
			'supplier': supplier_name,
			'vouchers': r.vouchers or '',
			'owed': net_balance,
			'paid': total_paid,
			'remaining': remaining,
			'latest_paid_at': latest_paid_at,
			'payments': payments_map.get(supplier_name, []),
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

	from core.models import PaymentReview, PaymentReviewStatus
	pending_reviews = PaymentReview.query.filter_by(
		created_by_id=getattr(current_user, 'id', None),
		status=PaymentReviewStatus.PENDING_REVIEW.value,
	).order_by(PaymentReview.created_at.desc()).limit(10).all()

	recent_settlements = (
		CassiteriteSupplierPayment.query
		.filter(
			CassiteriteSupplierPayment.is_deleted.is_(False),
			func.coalesce(CassiteriteSupplierPayment.is_advance, False).is_(False),
			CassiteriteSupplierPayment.stock_id.isnot(None),
		)
		.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
		.limit(12)
		.all()
	)

	return render_template(
		"cassiterite/pay_supplier.html",
		form=form,
		selected_stock_label=selected_stock_label,
		supplier_summaries=supplier_summaries,
		pending_reviews=pending_reviews,
		recent_settlements=recent_settlements,
		recent_suppliers=recent_suppliers,
		suppliers_pagination=suppliers_pagination,
		supplier_query=supplier_query,
	)


@cassiterite_bp.route('/pay_supplier/advance', methods=['GET', 'POST'])
@role_required('accountant')
def pay_supplier_advance():
	"""Record advance supplier payments for cassiterite stocks."""
	from cassiterite.forms import CassiteriteSupplierPaymentForm
	from flask_login import current_user
	from core.models import PaymentReview, PaymentReviewStatus, create_notification, User
	from utils import send_brevo_email_async

	form = CassiteriteSupplierPaymentForm()
	form.payment_kind.data = 'advance'

	stock_rows = (
		db.session.query(
			CassiteriteStock.id,
			CassiteriteStock.voucher_no,
			CassiteriteStock.supplier,
			CassiteriteStock.balance_to_pay,
		)
		.filter(CassiteriteStock.balance_to_pay > 0, CassiteriteStock.is_deleted.is_(False))
		.order_by(CassiteriteStock.date.desc())
		.all()
	)
	stock_ids = [r.id for r in stock_rows]
	supplier_summary_map = {}
	supplier_names = sorted({(row.supplier or '').strip() for row in stock_rows if (row.supplier or '').strip()})
	remaining_map = calculate_consolidated_supplier_remaining_balances(supplier_names)
	for row in stock_rows:
		key = (row.supplier or '').strip()
		if not key:
			continue
		summary = supplier_summary_map.setdefault(key, {'supplier': key, 'owed': 0.0, 'paid': 0.0, 'remaining': 0.0})
		summary['owed'] += float(row.balance_to_pay or 0)

	if supplier_names:
		payment_rows = []
		seen_payment_ids = set()
		if stock_ids:
			stock_payment_rows = (
				db.session.query(CassiteriteSupplierPayment, CassiteriteStock.supplier.label('stock_supplier'))
				.outerjoin(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
				.filter(
					CassiteriteSupplierPayment.is_deleted.is_(False),
					CassiteriteSupplierPayment.stock_id.in_(stock_ids),
				)
				.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
				.all()
			)
			payment_rows.extend(stock_payment_rows)

		standalone_rows = (
			db.session.query(CassiteriteSupplierPayment, CassiteriteStock.supplier.label('stock_supplier'))
			.outerjoin(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
			.filter(
				CassiteriteSupplierPayment.is_deleted.is_(False),
				CassiteriteSupplierPayment.stock_id.is_(None),
				CassiteriteSupplierPayment.supplier_name.in_(supplier_names),
			)
			.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
			.all()
		)
		payment_rows.extend(standalone_rows)

		for payment, stock_supplier in payment_rows:
			if payment.id in seen_payment_ids:
				continue
			seen_payment_ids.add(payment.id)
			key = (payment.supplier_name or stock_supplier or '').strip()
			if not key or key not in supplier_summary_map:
				continue
			supplier_summary_map[key]['paid'] += float(payment.amount_rwf or payment.amount or 0)

	for summary in supplier_summary_map.values():
		summary['remaining'] = float(remaining_map.get(' '.join(summary['supplier'].lower().split()), 0.0))

	form.existing_supplier.choices = build_consolidated_supplier_choices()
	form.stock_id.choices = []

	page = request.args.get('page', 1, type=int)
	recent_advances = (
		CassiteriteSupplierPayment.query
		.filter(
			CassiteriteSupplierPayment.is_advance.is_(True),
			CassiteriteSupplierPayment.is_deleted.is_(False),
		)
		.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
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
			return render_template('cassiterite/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)
		typed_new = (form.new_supplier.data or '').strip()
		selected_existing = (form.existing_supplier.data or '').strip()
		supplier = (typed_new or selected_existing or '').strip()
		if not supplier:
			flash('Please select an existing supplier or enter a new supplier name for advance payment.', 'danger')
			pending_reviews = PaymentReview.query.filter_by(created_by_id=getattr(current_user, 'id', None), status=PaymentReviewStatus.PENDING_REVIEW.value).order_by(PaymentReview.created_at.desc()).limit(10).all()
			return render_template('cassiterite/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)

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
					return render_template('cassiterite/pay_supplier_advance.html', form=form, pending_reviews=pending_reviews, recent_advances=recent_advances)

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
			mineral_type='cassiterite',
			type='Utanga ibicuruzwa',
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
				related_type='Kwishyura utanga ibicuruzwa(gasegereti)',
				related_id=review.id,
			)
		db.session.commit()

		boss_email = [boss_user.email] if boss_user and boss_user.email else ['boss@example.com']
		try:
			send_brevo_email_async(
				'Gusaba kwemeza igikorwa: Kwishyura advance supplier (Gasegereti)',
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
		return redirect(url_for('cassiterite.pay_supplier_advance'))

	pending_reviews = PaymentReview.query.filter_by(
		created_by_id=getattr(current_user, 'id', None),
		status=PaymentReviewStatus.PENDING_REVIEW.value,
	).order_by(PaymentReview.created_at.desc()).limit(10).all()
	return render_template(
		'cassiterite/pay_supplier_advance.html',
		form=form,
		pending_reviews=pending_reviews,
		recent_advances=recent_advances,
	)


@cassiterite_bp.route('/pay_supplier_advance/historical', methods=['GET', 'POST'])
@role_required('accountant', 'admin')
def pay_supplier_advance_historical():
	"""Record a historical supplier advance for cassiterite.

	Bypasses boss approval and cashier flows and marks the unified advance
	with `is_historical=True`.
	"""
	from flask_login import current_user
	from copper.models import CopperSupplier, CopperStock
	from cassiterite.models import CassiteriteSupplier, CassiteriteStock
	from core.models import UnifiedSupplierAdvance

	form = CassiteriteSupplierPaymentForm()

	page = request.args.get('page', 1, type=int)
	recent_advances = (
		CassiteriteSupplierPayment.query
		.filter(
			CassiteriteSupplierPayment.is_advance.is_(True),
			CassiteriteSupplierPayment.is_deleted.is_(False),
		)
		.order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
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
			return render_template('cassiterite/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)

		typed_new = (form.new_supplier.data or '').strip()
		selected_existing = (form.existing_supplier.data or '').strip()
		supplier = (typed_new or selected_existing or '').strip()
		if not supplier:
			flash('Please select an existing supplier or enter a new supplier name for advance payment.', 'danger')
			return render_template('cassiterite/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)

		supplier_id = _get_or_create_supplier_id(supplier)

		payment = CassiteriteSupplierPayment(
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
		return redirect(url_for('cassiterite.pay_supplier_advance_historical'))

	return render_template('cassiterite/pay_supplier_advance_historical.html', form=form, recent_advances=recent_advances)


@cassiterite_bp.route('/supplier/settlement/search.json')
@role_required('accountant')
def supplier_settlement_search():
	"""AJAX endpoint: get all suppliers (both active and with zero/negative balance).
	
	Returns all suppliers from stock history so settlements can be recorded for any supplier.
	Groups suppliers by normalized name to consolidate variations like:
	- "HAKIZIMANA" and "HAKIZIMANA J. PIERRE" → grouped as "HAKIZIMANA"
	"""
	from copper.models import CopperStock
	
	q = (request.args.get('q') or '').strip()
	
	# Query ALL copper stocks (no balance filter - settlement can be for any supplier)
	copper_suppliers = db.session.query(
		CopperStock.supplier.label('supplier'),
		func.coalesce(func.sum(CopperStock.net_balance), 0).label('total_net_balance')
	).filter(
		CopperStock.is_deleted.is_(False),
	).group_by(CopperStock.supplier).all()
	
	# Query ALL cassiterite stocks (no balance filter - settlement can be for any supplier)
	cass_suppliers = db.session.query(
		CassiteriteStock.supplier.label('supplier'),
		func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0).label('total_net_balance')
	).filter(
		CassiteriteStock.is_deleted.is_(False),
	).group_by(CassiteriteStock.supplier).all()
	
	# Merge suppliers and group by NORMALIZED name to consolidate variations
	supplier_map = {}  # normalized_name -> (canonical_name, total_balance)
	
	for r in copper_suppliers:
		supplier_name = (r.supplier or '').strip()
		if not supplier_name:
			continue
		normalized = normalize_counterparty_name(supplier_name)
		
		if normalized not in supplier_map:
			supplier_map[normalized] = (supplier_name, 0.0)
		
		current_name, balance = supplier_map[normalized]
		supplier_map[normalized] = (current_name, balance + float(r.total_net_balance or 0.0))
	
	for r in cass_suppliers:
		supplier_name = (r.supplier or '').strip()
		if not supplier_name:
			continue
		normalized = normalize_counterparty_name(supplier_name)
		
		if normalized not in supplier_map:
			supplier_map[normalized] = (supplier_name, 0.0)
		
		current_name, balance = supplier_map[normalized]
		supplier_map[normalized] = (current_name, balance + float(r.total_net_balance or 0.0))
	
	# Filter by query if provided (no balance filter - settlement can be for any supplier)
	results = []
	for normalized_name, (supplier_name, total_balance) in sorted(supplier_map.items()):
		if q and q.lower() not in supplier_name.lower():
			continue
		results.append({
			'supplier': supplier_name,
			'total_balance': f"{total_balance:,.2f}",
			'total_balance_raw': float(total_balance),
		})
	
	return safe_jsonify(results)



@cassiterite_bp.route('/record_supplier_settlement', methods=['GET', 'POST'])
@role_required('accountant', 'admin')
def record_supplier_settlement():
	"""Record a supplier settlement payment for all their unpaid stocks (bypasses approvals).
	
	The user selects a supplier, sees their total stock balance, optionally overrides
	the amount, chooses currency, and creates a settlement that is immediately disbursed.
	"""
	from cassiterite.models import CassiteriteStock
	
	if request.method == 'POST':
		supplier_name = (request.form.get('supplier_name') or '').strip()
		if not supplier_name:
			flash('Please select a supplier.', 'danger')
			return redirect(url_for('cassiterite.record_supplier_settlement'))
		
		# Get payment amount (user can specify any amount for settlement)
		try:
			payment_amount = float(request.form.get('payment_amount') or 0)
		except (TypeError, ValueError):
			payment_amount = 0
		
		if payment_amount <= 0:
			flash('Payment amount must be greater than 0.', 'danger')
			return redirect(url_for('cassiterite.record_supplier_settlement'))
		
		# Get currency and exchange rate
		currency = (request.form.get('currency') or 'RWF').upper()
		exchange_rate_input = request.form.get('exchange_rate') or None
		note = (request.form.get('note') or '').strip()
		
		try:
			amount_rwf, exchange_rate = _normalize_amount_to_rwf(payment_amount, currency, exchange_rate_input)
		except ValueError as exc:
			flash(str(exc), 'danger')
			return redirect(url_for('cassiterite.record_supplier_settlement'))
		
		# Create supplier if doesn't exist
		supplier_id = _get_or_create_supplier_id(supplier_name)
		
		# Create settlement payment (unlinked to specific stock, approved/disbursed immediately)
		try:
			payment = CassiteriteSupplierPayment(
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
			return redirect(url_for('cassiterite.record_supplier_settlement'))
		except Exception as e:
			db.session.rollback()
			flash(f'Error creating settlement: {str(e)}', 'danger')
			return redirect(url_for('cassiterite.record_supplier_settlement'))
	
	# GET request - show the form
	return render_template('cassiterite/record_supplier_settlement.html')


def calculate_supplier_remaining_balance(supplier_name):
	"""
	Calculate a supplier's remaining balance across all their stocks.
	Formula: Total Owed = SUM(stock.balance_to_pay - allocations) for all stocks
	         Total Paid = SUM(settlement payments only)
	         Remaining = Total Owed - Total Paid
	"""
	return calculate_consolidated_supplier_remaining_balance(supplier_name)


@cassiterite_bp.route('/supplier/payment/<int:payment_id>/receipt')
@role_required('accountant', 'cashier', 'boss', 'admin')
def supplier_receipt(payment_id):
	"""
	Shows a printable receipt for a supplier payment.
	Shows ALL stocks for the supplier + cumulative balance.
	"""
	from copper.models import CopperStock, CopperAdvanceAllocation

	payment = CassiteriteSupplierPayment.query.filter(
		CassiteriteSupplierPayment.id == payment_id,
		CassiteriteSupplierPayment.is_deleted.is_(False),
	).first()
	if not payment:
		return render_template('404.html'), 404

	# locate related stock and supplier name
	stock = None
	try:
		stock = CassiteriteStock.query.get(payment.stock_id) if payment.stock_id else None
	except Exception:
		stock = None
	supplier_name = getattr(payment, 'supplier_name', None) or (stock.supplier if stock else None) or 'Unknown'

	# allocations applied (for stock remaining and advance receipt details)
	allocated_from_this_advance = 0.0
	try:
		from cassiterite.models import CassiteriteAdvanceAllocation
		allocated_from_this_advance = db.session.query(
			func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0)
		).filter(CassiteriteAdvanceAllocation.supplier_payment_id == payment.id).scalar() or 0.0
	except Exception:
		try:
			db.session.rollback()
		except Exception:
			pass
		allocated_from_this_advance = 0.0

	# Advance receipt: do not compute stock balances using stock_id == None aggregates.
	if bool(payment.is_advance or not payment.stock_id) and not stock:
		remaining_before = 0.0
		remaining_after = max(float(payment.advance_remaining or 0.0), 0.0)
		applied_to_stock = float(allocated_from_this_advance or 0.0)
		all_supplier_stocks = []
		all_supplier_stocks.extend(CassiteriteStock.query.filter(
			CassiteriteStock.supplier == supplier_name,
			CassiteriteStock.is_deleted.is_(False)
		).all())
		all_supplier_stocks.extend(CopperStock.query.filter(
			CopperStock.supplier == supplier_name,
			CopperStock.is_deleted.is_(False)
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
			mineral_name = 'Cassiterite' if isinstance(s, CassiteriteStock) else 'Coltan'
			gross = float(getattr(s, 'amount_with_taxes', None) or getattr(s, 'amount', 0.0) or 0.0)
			transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
			rma = float(getattr(s, 'rma', 0.0) or 0.0)
			inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
			rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
			net = float(getattr(s, 'balance_to_pay', None) or getattr(s, 'net_balance', 0.0) or 0.0)
			deductions_rows.append({
				'mineral': mineral_name,
				'voucher_no': getattr(s, 'voucher_no', None) or str(getattr(s, 'id', '')),
				'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
				'percentage': float(getattr(s, 'percentage', 0.0) or 0.0),
				'nb': float(getattr(s, 'nb', 0.0) or 0.0),
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
	else:
		# Fetch ALL stocks for this supplier across both minerals so the receipt shows the full voucher/lot history.
		all_supplier_stocks = []
		all_supplier_stocks.extend(CassiteriteStock.query.filter(
			CassiteriteStock.supplier == supplier_name,
			CassiteriteStock.is_deleted.is_(False)
		).all())
		all_supplier_stocks.extend(CopperStock.query.filter(
			CopperStock.supplier == supplier_name,
			CopperStock.is_deleted.is_(False)
		).all())
		
		# Get allocations per stock (advance deductions)
		cass_stock_ids = [s.id for s in all_supplier_stocks if isinstance(s, CassiteriteStock)]
		copper_stock_ids = [s.id for s in all_supplier_stocks if isinstance(s, CopperStock)]
		allocations = []
		if cass_stock_ids:
			allocations.extend(db.session.query(
				CassiteriteAdvanceAllocation.stock_id,
				func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0).label('allocated')
			).filter(
				CassiteriteAdvanceAllocation.stock_id.in_(cass_stock_ids)
			).group_by(CassiteriteAdvanceAllocation.stock_id).all())
		if copper_stock_ids:
			allocations.extend(db.session.query(
				CopperAdvanceAllocation.stock_id,
				func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocated')
			).filter(
				CopperAdvanceAllocation.stock_id.in_(copper_stock_ids)
			).group_by(CopperAdvanceAllocation.stock_id).all())
		
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
			mineral_name = 'Cassiterite' if isinstance(s, CassiteriteStock) else 'Coltan'
			gross = float(getattr(s, 'amount_with_taxes', None) or getattr(s, 'amount', 0.0) or 0.0)
			transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
			rma = float(getattr(s, 'rma', 0.0) or 0.0)
			inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
			rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
			net = float(getattr(s, 'balance_to_pay', None) or getattr(s, 'net_balance', 0.0) or 0.0)
			
			deductions_rows.append({
				'mineral': mineral_name,
				'voucher_no': getattr(s, 'voucher_no', None) or str(getattr(s, 'id', '')),
				'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
				'percentage': float(getattr(s, 'percentage', 0.0) or 0.0),
				'nb': float(getattr(s, 'nb', 0.0) or 0.0),
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
		
		# Supplier-wide remaining balance using utility function
		remaining_before = float(calculate_supplier_remaining_balance(supplier_name) or 0.0)
		remaining_after = max(remaining_before - float(payment.amount_rwf or payment.amount or 0.0), 0.0)
		applied_to_stock = 0.0
		
		# All payments to this supplier (not just one stock)
		previous_payments = CassiteriteSupplierPayment.query.filter(
			CassiteriteSupplierPayment.supplier_name == supplier_name,
			CassiteriteSupplierPayment.is_deleted.is_(False),
			CassiteriteSupplierPayment.is_advance.is_(False),
			CassiteriteSupplierPayment.id != payment.id,
		).order_by(CassiteriteSupplierPayment.paid_at.desc()).all()
	
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


@cassiterite_bp.route('/pay_supplier/search.json')
@role_required('accountant')
def pay_supplier_search():
	"""AJAX endpoint: search suppliers by partial name and return remaining amount."""
	# use safe_jsonify to ensure Decimal -> float conversion
	from cassiterite.models import CassiteriteStock, CassiteriteSupplier

	q = (request.args.get('q') or '').strip()
	if not q:
		return safe_jsonify([])

	names = set()
	try:
		rows = db.session.query(CassiteriteStock.supplier).filter(CassiteriteStock.supplier.ilike(f"%{q}%")).distinct().all()
		names.update([r[0] for r in rows if r[0]])
	except Exception:
		try:
			db.session.rollback()
		except Exception:
			pass

	try:
		rows2 = db.session.query(CassiteriteSupplier.name).filter(CassiteriteSupplier.name.ilike(f"%{q}%")).distinct().all()
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


@cassiterite_bp.route('/pay_supplier/stock-search.json')
@role_required('accountant')
def pay_supplier_stock_search():
	"""AJAX endpoint: search cassiterite supplier obligations by voucher or supplier name."""
	q = (request.args.get('q') or '').strip()
	query = db.session.query(CassiteriteStock).filter(
		CassiteriteStock.is_deleted.is_(False),
		CassiteriteStock.balance_to_pay > 0,
	)
	if q:
		like_q = f"%{q}%"
		query = query.filter(or_(CassiteriteStock.voucher_no.ilike(like_q), CassiteriteStock.supplier.ilike(like_q)))

	results = []
	for stock in query.order_by(CassiteriteStock.date.desc()).limit(20).all():
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


@cassiterite_bp.route('/worker/payment/<int:payment_id>/receipt')
@role_required('accountant', 'cashier', 'boss', 'admin')
def worker_receipt(payment_id):
	"""Shows a printable receipt for a cassiterite worker payment."""
	payment = CassiteriteWorkerPayment.query.filter(
		CassiteriteWorkerPayment.id == payment_id,
		CassiteriteWorkerPayment.is_deleted.is_(False),
	).first()
	if not payment:
		return render_template('404.html'), 404
	return render_template('receipts/cassiterite_worker_receipt.html', payment=payment)


@cassiterite_bp.route('/supplier/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_supplier_payment(payment_id):
	from cassiterite.forms import CassiteriteSupplierPaymentForm
	from cassiterite.models.payment import CassiteriteSupplierPayment
	from cassiterite.models.stock import CassiteriteStock
	from core.models import PaymentReview

	payment = CassiteriteSupplierPayment.query.get_or_404(payment_id)
	form = CassiteriteSupplierPaymentForm()

	# populate choices
	# Fetch only required columns for choices to avoid hydrating full ORM objects
	stock_rows = (
		db.session.query(CassiteriteStock.id, CassiteriteStock.voucher_no, CassiteriteStock.supplier)
		.order_by(CassiteriteStock.date.desc())
		.all()
	)
	form.stock_id.choices = [
		(r[0], f"{r[1]} - {r[2]}") for r in stock_rows
	]

	if form.validate_on_submit():
		# require a reason for edits
		if not (form.change_reason.data and form.change_reason.data.strip()):
			flash('Change reason is required when editing a payment.', 'danger')
			return render_template('cassiterite/edit_supplier_payment.html', form=form, payment=payment)

		try:
			stock = CassiteriteStock.query.get(form.stock_id.data)
			payment.stock_id = stock.id
			payment.amount = form.amount.data
			payment.method = form.method.data
			payment.reference = form.reference.data
			payment.note = form.note.data
			db.session.add(payment)
			db.session.commit()

			# upsert pending PaymentReview for this edit so boss sees override
			from flask_login import current_user as _current_user
			from core.models import create_notification, User, PaymentReviewStatus, PaymentReview
			existing = PaymentReview.query.filter_by(
				payment_id=payment.id,
				status=PaymentReviewStatus.PENDING_REVIEW.value,
			).first()
			if existing:
				existing.mineral_type = 'cassiterite'
				existing.type = 'Utanga amabuye'
				existing.customer = stock.supplier
				existing.amount = payment.amount
				existing.currency = 'RWF'
				existing.created_by_id = getattr(_current_user, 'id', None)
				existing.boss_comment = (f"Edit requested: {form.change_reason.data.strip()}")
			else:
				review = PaymentReview(
					mineral_type='cassiterite',
					type='Utanga amabuye',
					customer=stock.supplier,
					amount=payment.amount,
					currency='RWF',
					payment_id=payment.id,
					created_by_id=getattr(_current_user, 'id', None),
					boss_comment=(f"Edit requested: {form.change_reason.data.strip()}"),
				)
				db.session.add(review)
			boss_user = User.query.filter_by(role='boss').first()
			if boss_user:
				create_notification(
					user_id=boss_user.id,
					type_='Ihindurwa ku  kwishyura utanga ibicuruzwa',
					message=f"Hasabwe gusuzuma: Impinduka ku kwishyura utanga ibicuruzwa - {stock.supplier}, Amafaranga: {payment.amount} RWF. Impamvu: {form.change_reason.data.strip()}",
					related_type='cassiterite_supplier_payment',
					related_id=payment.id,
				)
			db.session.commit()

			# best-effort email to boss
			try:
				from flask import current_app
				from utils import send_brevo_email_async
				boss_email = [boss_user.email] if boss_user and boss_user.email else ["boss@example.com"]
				subject = "Saba Kwemezwa: Impinduka kuri Kwishyura utanga Ibicuruzwa (Gasegereti)"
				html_content = (
					f"<p>Nyakubahwa Muyobozi,</p>"
					f"<p>Umucungamutungo {getattr(_current_user,'username','Unknown')} "
					f"yasabye ko musuzuma impinduka zikurikira kuri kwishyura utanga ibicuruzwa kuri Gasegereti:</p>"
					f"<p>Umutanga: {stock.supplier}<br>Amafaranga (byahinduwe): {payment.amount} RWF<br>Impamvu: {form.change_reason.data.strip()}</p>"
					"<p>Murakoze,<br>Mujye Muri system kwemeza iki gikorwa.<br>Urumuli Smart System</p>"
				)
				try:
					send_brevo_email_async(subject, html_content, boss_email)
				except Exception:
					import logging
					logging.exception("Failed to enqueue supplier edit email via Brevo")
					flash("Email notification failed; in-app notification saved.", "warning")
			except Exception:
				pass

			flash('Supplier payment updated; boss has been notified to review the change.', 'success')
			return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=stock.supplier))
		except Exception as e:
			db.session.rollback()
			flash(f'Error updating payment: {e}', 'danger')

	# pre-fill form
	if not form.is_submitted():
		form.stock_id.data = payment.stock_id
		form.amount.data = payment.amount
		form.method.data = payment.method
		form.reference.data = payment.reference
		form.note.data = payment.note

	return render_template('cassiterite/edit_supplier_payment.html', form=form, payment=payment)


@cassiterite_bp.route('/supplier/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_supplier_payment(payment_id):
	from cassiterite.models.payment import CassiteriteSupplierPayment
	from cassiterite.models.stock import CassiteriteStock
	from core.models import PaymentReview

	payment = CassiteriteSupplierPayment.query.get_or_404(payment_id)
	stock = CassiteriteStock.query.get(payment.stock_id)
	supplier = stock.supplier if stock else None
	# require a reason for deletion (submitted via hidden input)
	reason = request.form.get('change_reason', '')
	if not reason or not reason.strip():
		flash('Delete reason is required.', 'danger')
		return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier) if supplier else url_for('cassiterite.pay_supplier'))

	try:
		# Soft-delete payment and keep an auditable record for boss review/notification.
		from flask_login import current_user as _current_user
		from core.models import create_notification, User
		from core.models import PaymentReviewStatus, PaymentReview

		# capture fields before delete
		captured_amount = payment.amount
		captured_payment_id = payment.id

		payment.is_deleted = True
		payment.deleted_at = datetime.utcnow()
		payment.deleted_by_id = getattr(_current_user, 'id', None)
		payment.delete_reason = reason.strip()
		db.session.add(payment)
		db.session.commit()

		# create a PaymentReview so boss can see and audit the deletion
		existing = PaymentReview.query.filter_by(
			payment_id=captured_payment_id,
			status=PaymentReviewStatus.PENDING_REVIEW.value,
		).first()
		if existing:
			existing.mineral_type = 'cassiterite'
			existing.type = 'supplier'
			existing.customer = supplier
			existing.amount = captured_amount
			existing.currency = 'RWF'
			existing.created_by_id = getattr(_current_user, 'id', None)
			existing.boss_comment = (f"Delete requested: {reason.strip()}")
		else:
			review = PaymentReview(
				mineral_type='cassiterite',
				type='supplier',
				customer=supplier,
				amount=captured_amount,
				currency='RWF',
				payment_id=captured_payment_id,
				created_by_id=getattr(_current_user, 'id', None),
				boss_comment=(f"Delete requested: {reason.strip()}"),
			)
			db.session.add(review)

		boss_user = User.query.filter_by(role='boss').first()
		if boss_user:
			create_notification(
				user_id=boss_user.id,
				type_='Gusaba Gusiba igikorwa',
				message=f"Hasabwe gusuzuma: Gusiba kwishyura utanga amabuye  (Gasegereti) - {supplier}, Amafaranga: {captured_amount} RWF. Icyitonderwa: {reason.strip()}",
				related_type='cassiterite_supplier_payment',
				related_id=captured_payment_id,
			)
		db.session.commit()
		flash('Payment marked as deleted; boss has been notified for review.', 'success')
	except Exception as e:
		db.session.rollback()
		flash(f'Error submitting delete request: {e}', 'danger')

	if supplier:
		return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier))
	return redirect(url_for('cassiterite.pay_supplier'))


@cassiterite_bp.route('/worker/payment/<int:payment_id>/edit', methods=['GET', 'POST'])
@role_required('accountant')
def edit_worker_payment(payment_id):
	from cassiterite.forms import CassiteriteWorkerPaymentForm
	from cassiterite.models.workers_payment import CassiteriteWorkerPayment
	from core.models import PaymentReview, PaymentReviewStatus
	import json

	payment = CassiteriteWorkerPayment.query.get_or_404(payment_id)
	form = CassiteriteWorkerPaymentForm()

	if form.validate_on_submit():
		# require reason for edits
		if not (form.change_reason.data and form.change_reason.data.strip()):
			flash('Change reason is required when editing a payment.', 'danger')
			return render_template('cassiterite/edit_worker_payment.html', form=form, payment=payment)

		try:
			# Create approval request WITHOUT executing the edit
			from flask_login import current_user as _current_user
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
				mineral_type='cassiterite',
				type='expense_edit',
				customer=f"{payment.worker_name} (current) -> {form.worker_name.data} (proposed)",
				amount=float(form.amount.data or 0),
				currency='RWF',
				payment_id=payment.id,
				created_by_id=getattr(_current_user, 'id', None),
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
					message=f"Accountant {getattr(_current_user, 'username', 'unknown')} requested approval to edit expense for {payment.worker_name} (amount: {payment.amount} RWF -> {form.amount.data} RWF).",
					related_type="payment_review",
					related_id=review.id
				)
			
			db.session.commit()
			flash('Expense edit request submitted for boss approval. The edit will be executed after approval.', 'warning')
			return redirect(url_for('cassiterite.pay_worker'))
		except Exception as e:
			db.session.rollback()
			flash(f'Error submitting edit request: {e}', 'danger')

	if not form.is_submitted():
		form.worker_name.data = payment.worker_name
		form.amount.data = payment.amount
		form.method.data = payment.method
		form.reference.data = payment.reference
		form.note.data = payment.note

	return render_template('cassiterite/edit_worker_payment.html', form=form, payment=payment)


@cassiterite_bp.route('/worker/payment/<int:payment_id>/delete', methods=['POST'])
@role_required('accountant')
def delete_worker_payment(payment_id):
	from cassiterite.models.workers_payment import CassiteriteWorkerPayment
	from core.models import PaymentReview

	payment = CassiteriteWorkerPayment.query.get_or_404(payment_id)
	reason = request.form.get('change_reason', '')
	if not reason or not reason.strip():
		flash('Delete reason is required.', 'danger')
		return redirect(url_for('cassiterite.pay_worker'))

	try:
		# Soft-delete worker payment, then notify boss for review
		from flask_login import current_user as _current_user
		from core.models import create_notification, User
		from core.models import PaymentReviewStatus, PaymentReview

		# capture fields
		captured_amount = payment.amount
		captured_payment_id = payment.id
		captured_worker = payment.worker_name

		payment.is_deleted = True
		payment.deleted_at = datetime.utcnow()
		payment.deleted_by_id = getattr(_current_user, 'id', None)
		payment.delete_reason = reason.strip()
		db.session.add(payment)
		db.session.commit()

		existing = PaymentReview.query.filter_by(
			payment_id=captured_payment_id,
			status=PaymentReviewStatus.PENDING_REVIEW.value,
		).first()
		if existing:
			existing.mineral_type = None
			existing.type = 'worker'
			existing.customer = captured_worker
			existing.amount = captured_amount
			existing.currency = 'RWF'
			existing.created_by_id = getattr(_current_user, 'id', None)
			existing.boss_comment = (f"Delete requested: {reason.strip()}")
		else:
			review = PaymentReview(
				mineral_type=None,
				type='worker',
				customer=captured_worker,
				amount=captured_amount,
				currency='RWF',
				payment_id=captured_payment_id,
				created_by_id=getattr(_current_user, 'id', None),
				boss_comment=(f"Delete requested: {reason.strip()}"),
			)
			db.session.add(review)

		boss_user = User.query.filter_by(role='boss').first()
		if boss_user:
			create_notification(
				user_id=boss_user.id,
				type_='PAYMENT_DELETE_REQUEST',
				message=f"Hasabwe gusuzuma: Gusiba kwishyura umukozi - {captured_worker}, Amafaranga: {captured_amount} RWF. Icyitonderwa: {reason.strip()}",
				related_type='cassiterite_worker_payment',
				related_id=captured_payment_id,
			)
		db.session.commit()
		flash('Payment marked as deleted; boss has been notified for review.', 'success')
	except Exception as e:
		db.session.rollback()
		flash(f'Error submitting delete request: {e}', 'danger')

	return redirect(url_for('cassiterite.pay_worker'))


@cassiterite_bp.route("/supplier/<supplier>/ledger")
@role_required("accountant")
def cassiterite_supplier_ledger(supplier):
	supplier_name = (supplier or '').strip()
	if not supplier_name:
		flash('Supplier is required.', 'warning')
		return redirect(url_for('cassiterite.pay_supplier'))
	return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier_name))
