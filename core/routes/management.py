"""Core management routes (boss + store + notifications).

    copper_total_sales = (
single mineral module. They live under the `core` blueprint so that
`app.py` stays thin and focused on wiring, not logic.
"""

import json
import logging
from datetime import datetime, time, timedelta
import os

from flask import render_template, request, redirect, url_for, flash, abort, current_app as app
from flask_login import current_user
from werkzeug.utils import secure_filename

from config import db
from core.auth import role_required
from core.models import (
    BulkOutputPlan,
    PaymentReview,
    PaymentReviewStatus,
    User,
    create_notification,
    Notification,
    BulkPlanStatus,
    CustomerReceipt,
    CustomerReceiptType,
    CustomerReceiptChannel,
    fetch_user_notifications,
    StockChangeLog,
    Loan,
    CustomerUnearnedReceipt,
    CustomerUnearnedAllocation,
    BatchDeduction,
)
from . import core_bp
from sqlalchemy import func, or_, and_, case, cast, Integer, String, Float, select, union_all, literal
from sqlalchemy.orm import joinedload
from utils import safe_jsonify, close_name_matches, normalize_counterparty_name


logger = logging.getLogger(__name__)


def _build_transporter_ledger_rows(transporter_rows, opening_balance=None):
    display_rows = []
    running_balances = {}
    
    # Add opening balance row if provided (for 30-day view)
    if opening_balance is not None:
        transporter_name = (transporter_rows[0].transporter_name if transporter_rows else 'Unknown')
        transporter_key = ' '.join(transporter_name.strip().lower().split())
        running_balances[transporter_key] = float(opening_balance)
        display_rows.append({
            'id': None,
            'created_at': None,
            'transporter_name': transporter_name,
            'supplier_name': None,
            'entry_type': 'OPENING_BALANCE',
            'amount_input': 0.0,
            'currency': 'RWF',
            'exchange_rate': 1.0,
            'amount_rwf': 0.0,
            'running_balance_rwf': float(opening_balance),
            'note': 'Opening balance at start of period',
            'receipt_url': None,
            'is_opening_balance': True,
        })
    
    for row in sorted(transporter_rows or [], key=lambda r: ((r.created_at or datetime.min), int(getattr(r, 'id', 0) or 0))):
        transporter_name = row.transporter_name or 'Unknown'
        transporter_key = ' '.join(transporter_name.strip().lower().split())
        current_balance = float(running_balances.get(transporter_key, 0.0)) + float(row.amount_rwf or 0.0)
        running_balances[transporter_key] = current_balance
        display_rows.append({
            'id': row.id,
            'created_at': row.created_at,
            'transporter_name': row.transporter_name,
            'supplier_name': row.supplier_name,
            'entry_type': row.entry_type,
            'amount_input': float(row.amount_input or 0.0),
            'currency': (row.currency or 'RWF').upper(),
            'exchange_rate': float(row.exchange_rate or 1.0),
            'amount_rwf': float(row.amount_rwf or 0.0),
            'running_balance_rwf': float(current_balance),
            'note': row.note,
            'receipt_url': url_for('core.transporter_payment_receipt_detail', ledger_id=int(row.id)) if (row.entry_type or '').upper() in {'ADVANCE', 'CASH_PAYMENT'} else None,
            'is_opening_balance': False,
        })
    return display_rows

def _transporter_ledger_date_window(preset: str):
    preset = (preset or '30d').strip().lower()
    today = datetime.utcnow().date()
    if preset == 'all':
        return preset, None, None
    return '30d', today - timedelta(days=30), today


def _safe_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _review_details(review: PaymentReview) -> str:
    payload = _safe_payload(getattr(review, "request_payload", None))
    if not payload:
        # Backward compatibility for old rows where payload was stored in boss_comment.
        payload = _safe_payload(review.boss_comment)

    if payload:
        payment_kind = (payload.get("payment_kind") or "").strip().lower()
        if payment_kind == "advance":
            return "kwishyura advance"
        review_type = (review.type or "").strip().lower()
        action = (payload.get('action') or '').strip().lower()
        if review_type in {'cash_transaction', 'cash_collect_receipt', 'cash_supplier_refund'} or action in {'cash_transaction', 'collect_receipt', 'supplier_refund'}:
            if action == 'collect_receipt' or review_type == 'cash_collect_receipt':
                rid = payload.get('receipt_id')
                acc = payload.get('account_id')
                return f"Kwakira amafaranga (Cash Receipt) -> ashyirwa kuri konti #{acc}" + (f" | Receipt #{rid}" if rid else "")
            if action == 'supplier_refund' or review_type == 'cash_supplier_refund':
                return "supplier advance refund (cash in)"
            direction = (payload.get('direction') or '').strip().upper()
            if direction == 'OUT':
                return "manual cash OUT"
            return "manual cash IN"
        if "mukozi" in review_type or "worker" in review_type:
            return "kwishyura umukozi"
        if "supplier" in review_type or "utanga" in review_type:
            return "kwishyura supplier"

    if review.boss_comment:
        return review.boss_comment
    return "-"


def _review_amount_breakdown(review: PaymentReview) -> dict:
    payload = _safe_payload(getattr(review, "request_payload", None))
    if not payload:
        payload = _safe_payload(review.boss_comment)

    currency = (payload.get("currency") or review.currency or "RWF").upper()
    amount_rwf = float(payload.get("amount_rwf") or review.amount or 0)
    amount_input = float(payload.get("amount_input") or review.amount or 0)
    exchange_rate = float(payload.get("exchange_rate") or 0)

    if currency == "USD":
        note = f"{amount_rwf:,.2f} RWF"
        if exchange_rate:
            note = f"{amount_rwf:,.2f} RWF @ {exchange_rate:,.2f} RWF/USD"
        details = f"Original USD payment: {amount_input:,.2f} USD"
        if exchange_rate:
            details += f" | Exchange rate: {exchange_rate:,.2f} RWF/USD"
        return {
            "primary": f"{amount_input:,.2f} USD",
            "note": note,
            "details": details,
        }

    return {
        "primary": f"{amount_rwf:,.2f} RWF",
        "note": "",
        "details": "",
    }


def _parse_ledger_date(value: str | None):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return None


def _customer_ledger_filter_context():
    preset = (request.args.get('preset') or 'all').strip().lower()
    filter_from = _parse_ledger_date(request.args.get('from'))
    filter_to = _parse_ledger_date(request.args.get('to'))

    if preset == '30d' and not filter_from and not filter_to:
        filter_to = datetime.utcnow().date()
        filter_from = filter_to - timedelta(days=29)
    elif preset == 'all' and not request.args.get('from') and not request.args.get('to'):
        filter_from = None
        filter_to = None

    return preset, filter_from, filter_to


def _create_customer_unearned_receipt(
    customer: str,
    mineral_type: str | None,
    amount_input: float,
    currency: str,
    exchange_rate_input: float | None,
    payment_channel: str,
    note: str | None = None,
    batch_id: str | None = None,
):
    amount_rwf, exchange_rate = _normalize_amount_to_rwf(amount_input, currency, exchange_rate_input)
    row = CustomerUnearnedReceipt(
        mineral_type=(mineral_type or None),
        customer=customer,
        received_at=datetime.utcnow(),
        payment_channel=payment_channel,
        amount_input=float(amount_input),
        currency=currency,
        exchange_rate=float(exchange_rate or 1.0),
        amount_rwf=float(amount_rwf),
        remaining_rwf=float(amount_rwf),
        note=note,
        proof_image_path=None,
        proof_uploaded_at=None,
        created_by_id=getattr(current_user, 'id', None),
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()

    batch_id_form = (batch_id or '').strip() or None
    if batch_id_form:
        try:
            applied_amt = float(row.remaining_rwf or 0.0)
            if applied_amt > 0:
                alloc = CustomerUnearnedAllocation(
                    unearned_id=int(row.id),
                    batch_id=batch_id_form,
                    stock_mineral_type=(row.mineral_type or '').strip().lower() or None,
                    applied_amount_rwf=float(applied_amt),
                    created_by_id=getattr(current_user, 'id', None),
                    created_at=datetime.utcnow(),
                )
                db.session.add(alloc)
                row.remaining_rwf = float(row.remaining_rwf or 0.0) - float(applied_amt)
                if row.remaining_rwf < 0:
                    row.remaining_rwf = 0.0
                db.session.flush()
        except Exception:
            logger.exception('customer_unearned_receipts: failed to create allocation for batch')

    return row


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
ALLOWED_ROLES = ["admin", "boss", "accountant", "store_keeper", "negotiator", "cashier"]


def _normalize_amount_to_rwf(amount, currency, exchange_rate):
    currency_code = (currency or "RWF").upper()
    input_amount = float(amount or 0)
    # Normalize exchange_rate argument which may be None, empty string, or a numeric string
    def _parse_rate(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if s == '':
            return None
        # allow comma as thousands or decimal separators by removing commas
        s = s.replace(',', '')
        try:
            return float(s)
        except Exception:
            return None

    rate = _parse_rate(exchange_rate)

    if currency_code == "RWF":
        # For local currency, exchange rate is irrelevant — return 1.0
        return input_amount, float(rate or 1.0)
    if currency_code == "USD":
        if rate is None or rate <= 0:
            raise ValueError("Exchange rate is required and must be greater than 0 for USD transactions.")
        return input_amount * rate, rate
    raise ValueError(f"Unsupported currency: {currency_code}")


def _flash_and_notify(message: str, category: str = 'info') -> None:
    """Flash a message and persist it as a Notification for the current user.

    This ensures important UI flashes are also queryable from dedicated
    notification pages and persisted across redirects.
    """
    try:
        flash(message, category)
        if getattr(current_user, 'is_authenticated', False):
            try:
                create_notification(getattr(current_user, 'id', None), 'flash', message)
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    logger.exception("_flash_and_notify: rollback failed")
                logger.exception("_flash_and_notify: failed to create notification")
    except Exception:
        logger.exception("_flash_and_notify: unexpected error while flashing/notifying")




@core_bp.route("/accountant/suppliers/lookup", methods=["GET"])
@role_required("accountant", "boss", "admin")
def consolidated_supplier_ledger_lookup():
    supplier = (request.args.get("supplier") or "").strip()
    if not supplier:
        flash("Utanga ibicuruzwa ntabuze niba.", "warning")
        return redirect(url_for("core.boss_dashboard"))

    from utils import generate_supplier_slug, normalize_counterparty_name

    supplier_slug = generate_supplier_slug(supplier)
    if not supplier_slug:
        supplier_slug = normalize_counterparty_name(supplier)
    return redirect(url_for("core.consolidated_supplier_ledger", supplier_norm=supplier_slug))


def _resolve_supplier_ledger_identity(supplier_input: str):
    from core.models import UnifiedSupplierAdvance
    from utils import generate_supplier_slug, normalize_counterparty_name

    input_value = (supplier_input or "").strip()
    if not input_value:
        return None

    normalized_input = normalize_counterparty_name(input_value)
    slug_candidates = []
    for candidate in (input_value.lower(), generate_supplier_slug(input_value)):
        candidate = (candidate or "").strip().lower()
        if candidate and candidate not in slug_candidates:
            slug_candidates.append(candidate)

    query_filters = []
    if slug_candidates:
        query_filters.append(UnifiedSupplierAdvance.supplier_slug.in_(slug_candidates))
    if normalized_input:
        query_filters.append(UnifiedSupplierAdvance.supplier_name_norm == normalized_input)
    query_filters.append(func.lower(func.trim(UnifiedSupplierAdvance.supplier_name)) == input_value.lower())

    row = (
        db.session.query(
            UnifiedSupplierAdvance.supplier_name,
            UnifiedSupplierAdvance.supplier_name_norm,
            UnifiedSupplierAdvance.supplier_slug,
        )
        .filter(
            UnifiedSupplierAdvance.is_deleted.is_(False),
            or_(*query_filters),
        )
        .order_by(UnifiedSupplierAdvance.paid_at.desc(), UnifiedSupplierAdvance.id.desc())
        .first()
    )
    if row:
        return row

    fallback_slug = generate_supplier_slug(input_value) or normalized_input
    return input_value, normalized_input, fallback_slug


@core_bp.route("/accountant/suppliers/suggest", methods=["GET"])
@role_required("accountant", "boss", "admin")
def supplier_name_suggest():
    q = (request.args.get("q") or "").strip()
    with_balances = (request.args.get("with_balances") or "").strip() in {"1", "true", "yes"}
    if len(q) < 1:
        return safe_jsonify({"results": []})

    try:
        from utils import generate_supplier_slug, sql_normalize_counterparty_expr
        from copper.models import CopperSupplier
        from cassiterite.models import CassiteriteSupplier
        from copper.models import CopperStock
        from cassiterite.models import CassiteriteStock
        from core.models import UnifiedSupplierAdvance

        pattern = f"%{q}%"
        normalized_q = normalize_counterparty_name(q)
        normalized_pattern = f"%{'%'.join(normalized_q.split())}%" if normalized_q else pattern
        slug_q = generate_supplier_slug(q)

        copper_name_expr = sql_normalize_counterparty_expr(CopperSupplier.name)
        cass_name_expr = sql_normalize_counterparty_expr(CassiteriteSupplier.name)
        copper_stock_expr = sql_normalize_counterparty_expr(CopperStock.supplier)
        cass_stock_expr = sql_normalize_counterparty_expr(CassiteriteStock.supplier)
        unified_name_expr = sql_normalize_counterparty_expr(UnifiedSupplierAdvance.supplier_name)
        copper_rows = (
            db.session.query(CopperSupplier.name)
            .filter(CopperSupplier.is_deleted.is_(False))
            .filter(or_(CopperSupplier.name.ilike(pattern), copper_name_expr.ilike(normalized_pattern)))
            .order_by(CopperSupplier.name.asc())
            .limit(10)
            .all()
        )
        cass_rows = (
            db.session.query(CassiteriteSupplier.name)
            .filter(CassiteriteSupplier.is_deleted.is_(False))
            .filter(or_(CassiteriteSupplier.name.ilike(pattern), cass_name_expr.ilike(normalized_pattern)))
            .order_by(CassiteriteSupplier.name.asc())
            .limit(10)
            .all()
        )

        unified_rows = (
            db.session.query(UnifiedSupplierAdvance.supplier_name)
            .filter(UnifiedSupplierAdvance.is_deleted.is_(False))
            .filter(
                or_(
                    UnifiedSupplierAdvance.supplier_name.ilike(pattern),
                    unified_name_expr.ilike(normalized_pattern),
                    UnifiedSupplierAdvance.supplier_name_norm == normalized_q,
                    UnifiedSupplierAdvance.supplier_slug == slug_q,
                )
            )
            .order_by(UnifiedSupplierAdvance.paid_at.desc(), UnifiedSupplierAdvance.id.desc())
            .limit(10)
            .all()
        )

        # Also include suppliers that exist only on stock rows (user typed them during stock entry)
        # so suggestions work immediately across modules.
        copper_stock_rows = (
            db.session.query(func.trim(CopperStock.supplier))
            .filter(CopperStock.is_deleted.is_(False))
            .filter(or_(func.trim(CopperStock.supplier).ilike(pattern), copper_stock_expr.ilike(normalized_pattern)))
            .group_by(func.trim(CopperStock.supplier))
            .order_by(func.trim(CopperStock.supplier).asc())
            .limit(10)
            .all()
        )
        cass_stock_rows = (
            db.session.query(func.trim(CassiteriteStock.supplier))
            .filter(CassiteriteStock.is_deleted.is_(False))
            .filter(or_(func.trim(CassiteriteStock.supplier).ilike(pattern), cass_stock_expr.ilike(normalized_pattern)))
            .group_by(func.trim(CassiteriteStock.supplier))
            .order_by(func.trim(CassiteriteStock.supplier).asc())
            .limit(10)
            .all()
        )

        seen = set()
        results = []
        for (name,) in (copper_rows or []):
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name})
        for (name,) in (cass_rows or []):
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name})

        for (name,) in (unified_rows or []):
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name})

        for (name,) in (copper_stock_rows or []):
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name})

        for (name,) in (cass_stock_rows or []):
            if not name:
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({"name": name})

        results = results[:10]

        if with_balances and results:
            norms = {" ".join((r.get("name") or "").strip().lower().split()) for r in results}
            norms = {n for n in norms if n}

            agg_rows = []
            if norms:
                agg_rows = (
                    db.session.query(
                        UnifiedSupplierAdvance.supplier_name_norm.label("supplier_norm"),
                        func.max(UnifiedSupplierAdvance.supplier_name).label("supplier_name"),
                        func.coalesce(func.sum(UnifiedSupplierAdvance.advance_remaining), 0).label("wallet_remaining"),
                        func.coalesce(func.sum(UnifiedSupplierAdvance.amount_rwf), 0).label("net_ledger_rwf"),
                        func.max(UnifiedSupplierAdvance.paid_at).label("last_activity"),
                    )
                    .filter(
                        UnifiedSupplierAdvance.is_deleted.is_(False),
                        UnifiedSupplierAdvance.supplier_name_norm.in_(sorted(norms)),
                    )
                    .group_by(UnifiedSupplierAdvance.supplier_name_norm)
                    .all()
                )

            agg_map = {
                (r.supplier_norm or "").strip().lower(): {
                    "wallet_remaining": float(r.wallet_remaining or 0),
                    "net_ledger_rwf": float(r.net_ledger_rwf or 0),
                    "last_activity": r.last_activity,
                }
                for r in (agg_rows or [])
                if (r.supplier_norm or "").strip()
            }

            enriched = []
            for r in results:
                name = (r.get("name") or "").strip()
                norm = " ".join(name.lower().split())
                meta = agg_map.get(norm) or {}
                out = {"name": name}
                out.update(
                    {
                        "wallet_remaining": meta.get("wallet_remaining", 0.0),
                        "net_ledger_rwf": meta.get("net_ledger_rwf", 0.0),
                        "last_activity": meta.get("last_activity"),
                    }
                )
                enriched.append(out)
            results = enriched

        return safe_jsonify({"results": results})
    except Exception:
        return safe_jsonify({"results": []})


@core_bp.route("/accountant/vouchers/suggest", methods=["GET"])
@role_required("accountant", "store_keeper", "boss", "admin")
def voucher_suggest():
    """AJAX endpoint: search vouchers and return supplier name + voucher details.
    Also shows deleted records so user knows why a voucher is blocked."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return safe_jsonify({"results": []})

    try:
        from copper.models import CopperStock
        from cassiterite.models import CassiteriteStock

        pattern = f"%{q}%"
        
        # Search copper stocks by voucher (include deleted to show blocked vouchers)
        copper_rows = (
            db.session.query(
                CopperStock.voucher_no.label("voucher"),
                CopperStock.supplier.label("supplier"),
                CopperStock.input_kg.label("qty"),
                CopperStock.percentage.label("percentage"),
                CopperStock.is_deleted.label("is_deleted"),
            )
            .filter(CopperStock.voucher_no.ilike(pattern))
            .order_by(CopperStock.is_deleted.asc(), CopperStock.voucher_no.desc())
            .limit(20)
            .all()
        )
        
        # Search cassiterite stocks by voucher (include deleted to show blocked vouchers)
        cass_rows = (
            db.session.query(
                CassiteriteStock.voucher_no.label("voucher"),
                CassiteriteStock.supplier.label("supplier"),
                CassiteriteStock.input_kg.label("qty"),
                CassiteriteStock.percentage.label("percentage"),
                CassiteriteStock.is_deleted.label("is_deleted"),
            )
            .filter(CassiteriteStock.voucher_no.ilike(pattern))
            .order_by(CassiteriteStock.is_deleted.asc(), CassiteriteStock.voucher_no.desc())
            .limit(20)
            .all()
        )

        results = []
        seen = set()
        
        for row in (copper_rows or []):
            if not row.voucher:
                continue
            key = (row.voucher or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            status = "DELETED" if row.is_deleted else ""
            results.append({
                "voucher": row.voucher,
                "supplier": f"{row.supplier}{' [DELETED]' if row.is_deleted else ''}",
                "qty": float(row.qty or 0),
                "percentage": float(row.percentage or 0),
                "type": "Copper",
                "is_deleted": row.is_deleted,
            })
        
        for row in (cass_rows or []):
            if not row.voucher:
                continue
            key = (row.voucher or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "voucher": row.voucher,
                "supplier": f"{row.supplier}{' [DELETED]' if row.is_deleted else ''}",
                "qty": float(row.qty or 0),
                "percentage": float(row.percentage or 0),
                "type": "Cassiterite",
                "is_deleted": row.is_deleted,
            })
        
        results = results[:15]
        return safe_jsonify({"results": results})
    except Exception:
        return safe_jsonify({"results": []})


@core_bp.route("/accountant/suppliers/<path:supplier_norm>", methods=["GET"])
@role_required("accountant", "boss", "admin")
def consolidated_supplier_ledger(supplier_norm: str):
    from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation
    from utils import calculate_consolidated_supplier_remaining_balance, normalize_counterparty_name, sql_normalize_counterparty_expr

    # Resolve supplier name from parameter (could be slug, normalized name, or direct name)
    input_norm = (supplier_norm or '').strip().lower()
    if not input_norm:
        abort(404)

    resolved = _resolve_supplier_ledger_identity(input_norm)
    if not resolved:
        abort(404)

    supplier_name, norm, supplier_slug = resolved
    norm = norm or normalize_counterparty_name(supplier_name or input_norm)

    if not norm:
        abort(404)

    supplier_like = f"%{'%'.join(norm.split())}%"
    norm_slug = '-'.join(norm.split())
    advance_supplier_filter = or_(
        UnifiedSupplierAdvance.supplier_name_norm == norm,
        UnifiedSupplierAdvance.supplier_slug == norm_slug,
        func.lower(func.trim(UnifiedSupplierAdvance.supplier_name)).ilike(supplier_like),
    )

    # Ledger filters (default to recent activity for better UX/performance)
    preset = (request.args.get('preset') or '30d').strip().lower()
    voucher_q = (request.args.get('voucher') or '').strip()
    mineral_q = (request.args.get('mineral') or '').strip().lower()
    from_raw = (request.args.get('from') or '').strip()
    to_raw = (request.args.get('to') or '').strip()

    today = datetime.utcnow().date()
    filter_from = None
    filter_to = None

    def _parse_ymd(value: str):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except Exception:
            return None

    if from_raw:
        filter_from = _parse_ymd(from_raw)
    if to_raw:
        filter_to = _parse_ymd(to_raw)

    # Apply preset only if explicit from/to not provided
    if not filter_from and not filter_to:
        if preset == 'all':
            filter_from = None
            filter_to = None
        elif preset == '90d':
            filter_from = today - timedelta(days=90)
            filter_to = today
        elif preset == 'month':
            filter_from = today.replace(day=1)
            filter_to = today
        else:
            preset = '30d'
            filter_from = today - timedelta(days=30)
            filter_to = today
    else:
        preset = 'custom'
        if filter_from and not filter_to:
            filter_to = today
        if filter_to and not filter_from:
            filter_from = filter_to - timedelta(days=30)

    row_limit = 5000 if preset == 'all' else 1000

    advances = (
        UnifiedSupplierAdvance.query
        .filter(
            UnifiedSupplierAdvance.is_deleted.is_(False),
            advance_supplier_filter,
        )
        .order_by(UnifiedSupplierAdvance.paid_at.desc(), UnifiedSupplierAdvance.id.desc())
        .all()
    )

    supplier_name = None
    if advances:
        supplier_name = advances[0].supplier_name

    # Some suppliers may exist only as text on stock rows (or only have settlement payments)
    # and therefore have no unified advances yet. Still render the ledger instead of 404.
    if not supplier_name:
        try:
            from copper.models import CopperSupplier, CopperStock
            from cassiterite.models import CassiteriteSupplier, CassiteriteStock

            supplier_name = (
                db.session.query(func.max(CopperSupplier.name))
                .filter(CopperSupplier.is_deleted.is_(False), func.lower(func.trim(CopperSupplier.name)) == norm)
                .scalar()
            )
            if not supplier_name:
                supplier_name = (
                    db.session.query(func.max(CassiteriteSupplier.name))
                    .filter(CassiteriteSupplier.is_deleted.is_(False), func.lower(func.trim(CassiteriteSupplier.name)) == norm)
                    .scalar()
                )
            if not supplier_name:
                supplier_name = (
                    db.session.query(func.max(func.trim(CopperStock.supplier)))
                    .filter(CopperStock.is_deleted.is_(False), func.lower(CopperStock.supplier).ilike(supplier_like))
                    .scalar()
                )
            if not supplier_name:
                supplier_name = (
                    db.session.query(func.max(func.trim(CassiteriteStock.supplier)))
                    .filter(CassiteriteStock.is_deleted.is_(False), func.lower(CassiteriteStock.supplier).ilike(supplier_like))
                    .scalar()
                )
        except Exception:
            supplier_name = None

    if not supplier_name:
        supplier_name = (supplier_norm or '').strip() or norm

    # If legacy data stored supplier_name as slug, display canonical spaced form.
    if supplier_name and ('-' in supplier_name) and (' ' not in supplier_name) and ('/' not in supplier_name):
        supplier_name = ' '.join((norm or supplier_name).split())

    supplier_remaining = calculate_consolidated_supplier_remaining_balance(supplier_name)

    wallet_remaining = float(sum([float(a.advance_remaining or 0.0) for a in (advances or [])]) or 0.0)
    total_advanced = float(sum([float(a.amount_rwf or 0.0) for a in (advances or []) if float(a.amount_rwf or 0.0) > 0]) or 0.0)
    total_refunded = float(
        sum([
            abs(float(a.amount_rwf or 0.0))
            for a in (advances or [])
            if float(a.amount_rwf or 0.0) < 0
        ])
        or 0.0
    )

    allocation_rows = (
        db.session.query(
            UnifiedSupplierAdvanceAllocation.stock_mineral_type,
            func.coalesce(func.sum(UnifiedSupplierAdvanceAllocation.applied_amount), 0),
        )
        .join(UnifiedSupplierAdvance, UnifiedSupplierAdvance.id == UnifiedSupplierAdvanceAllocation.advance_id)
        .filter(
            UnifiedSupplierAdvance.is_deleted.is_(False),
            advance_supplier_filter,
        )
        .group_by(UnifiedSupplierAdvanceAllocation.stock_mineral_type)
        .order_by(UnifiedSupplierAdvanceAllocation.stock_mineral_type.asc())
        .all()
    )

    copper_debt = 0.0
    cassiterite_debt = 0.0
    try:
        from copper.models import CopperStock
        copper_stocks_q = (
            CopperStock.query
            .filter(
                CopperStock.is_deleted.is_(False),
                sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like),
            )
        )
        if filter_from:
            copper_stocks_q = copper_stocks_q.filter(CopperStock.date >= filter_from)
        if filter_to:
            copper_stocks_q = copper_stocks_q.filter(CopperStock.date <= filter_to)
        copper_stocks = (
            copper_stocks_q
            .order_by(CopperStock.date.desc(), CopperStock.id.desc())
            .limit(row_limit)
            .all()
        )
        # Debt is always computed on the full ledger; keep this value stable.
        try:
            copper_debt = float(sum([max(float(s.remaining_to_pay() or 0.0), 0.0) for s in (
                CopperStock.query
                .filter(
                    CopperStock.is_deleted.is_(False),
                    sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like),
                )
                .order_by(CopperStock.date.desc(), CopperStock.id.desc())
                .limit(500)
                .all()
            )]) or 0.0)
        except Exception:
            copper_debt = 0.0
    except Exception:
        copper_debt = 0.0

    try:
        from cassiterite.models import CassiteriteStock
        cass_stocks_q = (
            CassiteriteStock.query
            .filter(
                CassiteriteStock.is_deleted.is_(False),
                sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like),
            )
        )
        if filter_from:
            cass_stocks_q = cass_stocks_q.filter(CassiteriteStock.date >= filter_from)
        if filter_to:
            cass_stocks_q = cass_stocks_q.filter(CassiteriteStock.date <= filter_to)
        cass_stocks = (
            cass_stocks_q
            .order_by(CassiteriteStock.date.desc(), CassiteriteStock.id.desc())
            .limit(row_limit)
            .all()
        )
        try:
            cassiterite_debt = float(sum([max(float(s.remaining_to_pay() or 0.0), 0.0) for s in (
                CassiteriteStock.query
                .filter(
                    CassiteriteStock.is_deleted.is_(False),
                    sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like),
                )
                .order_by(CassiteriteStock.date.desc(), CassiteriteStock.id.desc())
                .limit(500)
                .all()
            )]) or 0.0)
        except Exception:
            cassiterite_debt = 0.0
    except Exception:
        cassiterite_debt = 0.0

    # Build a consolidated supplier ledger stream across minerals.
    # Debit increases what the company owes the supplier; credit decreases it.
    ledger_events: list[dict] = []

    # Batch build voucher maps for allocation rendering.
    copper_stock_map = {int(s.id): s for s in (copper_stocks or [])}
    cass_stock_map = {int(s.id): s for s in (cass_stocks or [])}

    # Stock events (debits)
    for s in (copper_stocks or []):
        ledger_events.append({
            "date": getattr(s, "date", None),
            "sort_key": 1,
            "mineral": "coltan",
            "description": f"Stock {getattr(s, 'voucher_no', '-')}",
            "debit": float(getattr(s, "net_balance", 0.0) or 0.0),
            "credit": 0.0,
        })
    for s in (cass_stocks or []):
        ledger_events.append({
            "date": getattr(s, "date", None),
            "sort_key": 1,
            "mineral": "cassiterite",
            "description": f"Stock {getattr(s, 'voucher_no', '-')}",
            "debit": float(getattr(s, "balance_to_pay", 0.0) or 0.0),
            "credit": 0.0,
        })

    # Supplier-level deductions (retention / business fees) not tied to a batch
    try:
        from core.models import SupplierDeduction
        supplier_deds = (
            db.session.query(SupplierDeduction)
            .filter(SupplierDeduction.supplier_name.ilike(supplier_like))
            .order_by(SupplierDeduction.created_at.desc())
            .limit(row_limit)
            .all()
        )
        for d in (supplier_deds or []):
            amt = float(getattr(d, 'amount_rwf', 0.0) or 0.0)
            ledger_events.append({
                'date': getattr(d, 'created_at', None),
                'sort_key': 2,
                'mineral': '-',
                'description': f"{(getattr(d, 'deduction_type') or 'DEDUCTION')} (Supplier-level)",
                'debit': 0.0,
                'credit': amt,
                'original_currency': getattr(d, 'currency', 'RWF'),
                'original_amount': float(getattr(d, 'amount_input', 0.0) or 0.0),
            })
    except Exception:
        pass

    # Settlement payments (credits) - include both linked and unlinked settlements
    try:
        from copper.models import SupplierPayment as CopperSupplierPayment, CopperStock

        # Query for linked settlements (old style - linked to specific stock)
        copper_linked_q = (
            db.session.query(CopperSupplierPayment, CopperStock)
            .join(CopperStock, CopperStock.id == CopperSupplierPayment.stock_id)
            .filter(
                CopperSupplierPayment.is_deleted.is_(False),
                CopperSupplierPayment.stock_id.isnot(None),
                CopperStock.is_deleted.is_(False),
                func.lower(CopperStock.supplier).ilike(supplier_like),
            )
        )
        if filter_from:
            copper_linked_q = copper_linked_q.filter(CopperSupplierPayment.paid_at >= datetime.combine(filter_from, time.min))
        if filter_to:
            copper_linked_q = copper_linked_q.filter(CopperSupplierPayment.paid_at <= datetime.combine(filter_to, time.max))

        copper_linked = (
            copper_linked_q
            .order_by(CopperSupplierPayment.paid_at.desc(), CopperSupplierPayment.id.desc())
            .limit(row_limit)
            .all()
        )
        
        # Query for unlinked settlements (new style - not tied to specific stock)
        copper_unlinked_q = (
            db.session.query(CopperSupplierPayment)
            .filter(
                CopperSupplierPayment.is_deleted.is_(False),
                CopperSupplierPayment.stock_id.is_(None),
                CopperSupplierPayment.is_advance.is_(False),  # Only settlements, not advances
                func.lower(func.coalesce(CopperSupplierPayment.supplier_name, '')).ilike(supplier_like),
            )
        )
        if filter_from:
            copper_unlinked_q = copper_unlinked_q.filter(CopperSupplierPayment.paid_at >= datetime.combine(filter_from, time.min))
        if filter_to:
            copper_unlinked_q = copper_unlinked_q.filter(CopperSupplierPayment.paid_at <= datetime.combine(filter_to, time.max))

        copper_unlinked = (
            copper_unlinked_q
            .order_by(CopperSupplierPayment.paid_at.desc(), CopperSupplierPayment.id.desc())
            .limit(row_limit)
            .all()
        )
        
        # Process linked settlements
        for p, s in copper_linked:
            amt = float(getattr(p, "amount_rwf", None) or getattr(p, "amount", 0.0) or 0.0)
            original_currency = (getattr(p, "currency", None) or "RWF").upper()
            original_amount = float(
                getattr(p, "input_amount", None)
                or getattr(p, "amount", 0.0)
                or getattr(p, "amount_rwf", 0.0)
                or 0.0
            )
            ledger_events.append({
                "date": getattr(p, "paid_at", None),
                "sort_key": 2,
                "mineral": "coltan",
                "description": f"Settlement Payment (Stock {getattr(s, 'voucher_no', '-')}) (Ref: {getattr(p, 'reference', None) or '-'})",
                "debit": 0.0,
                "credit": amt,
                "original_currency": original_currency,
                "original_amount": original_amount,
            })
        
        # Process unlinked settlements
        for p in copper_unlinked:
            amt = float(getattr(p, "amount_rwf", None) or getattr(p, "amount", 0.0) or 0.0)
            original_currency = (getattr(p, "currency", None) or "RWF").upper()
            original_amount = float(
                getattr(p, "input_amount", None)
                or getattr(p, "amount", 0.0)
                or getattr(p, "amount_rwf", 0.0)
                or 0.0
            )
            ledger_events.append({
                "date": getattr(p, "paid_at", None),
                "sort_key": 2,
                "mineral": "coltan",
                "description": f"Settlement Payment (All stocks) (Ref: {getattr(p, 'reference', None) or '-'})",
                "debit": 0.0,
                "credit": amt,
                "original_currency": original_currency,
                "original_amount": original_amount,
            })
    except Exception:
        pass

    try:
        from cassiterite.models import CassiteriteSupplierPayment, CassiteriteStock

        # Query for linked settlements (old style - linked to specific stock)
        cass_linked_q = (
            db.session.query(CassiteriteSupplierPayment, CassiteriteStock)
            .join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
            .filter(
                CassiteriteSupplierPayment.is_deleted.is_(False),
                CassiteriteSupplierPayment.stock_id.isnot(None),
                CassiteriteStock.is_deleted.is_(False),
                func.lower(CassiteriteStock.supplier).ilike(supplier_like),
            )
        )
        if filter_from:
            cass_linked_q = cass_linked_q.filter(CassiteriteSupplierPayment.paid_at >= datetime.combine(filter_from, time.min))
        if filter_to:
            cass_linked_q = cass_linked_q.filter(CassiteriteSupplierPayment.paid_at <= datetime.combine(filter_to, time.max))

        cass_linked = (
            cass_linked_q
            .order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
            .limit(row_limit)
            .all()
        )
        
        # Query for unlinked settlements (new style - not tied to specific stock)
        cass_unlinked_q = (
            db.session.query(CassiteriteSupplierPayment)
            .filter(
                CassiteriteSupplierPayment.is_deleted.is_(False),
                CassiteriteSupplierPayment.stock_id.is_(None),
                CassiteriteSupplierPayment.is_advance.is_(False),  # Only settlements, not advances
                func.lower(func.coalesce(CassiteriteSupplierPayment.supplier_name, '')).ilike(supplier_like),
            )
        )
        if filter_from:
            cass_unlinked_q = cass_unlinked_q.filter(CassiteriteSupplierPayment.paid_at >= datetime.combine(filter_from, time.min))
        if filter_to:
            cass_unlinked_q = cass_unlinked_q.filter(CassiteriteSupplierPayment.paid_at <= datetime.combine(filter_to, time.max))

        cass_unlinked = (
            cass_unlinked_q
            .order_by(CassiteriteSupplierPayment.paid_at.desc(), CassiteriteSupplierPayment.id.desc())
            .limit(row_limit)
            .all()
        )
        
        # Process linked settlements
        for p, s in cass_linked:
            amt = float(getattr(p, "amount_rwf", None) or getattr(p, "amount", 0.0) or 0.0)
            original_currency = (getattr(p, "currency", None) or "RWF").upper()
            original_amount = float(
                getattr(p, "input_amount", None)
                or getattr(p, "amount", 0.0)
                or getattr(p, "amount_rwf", 0.0)
                or 0.0
            )
            ledger_events.append({
                "date": getattr(p, "paid_at", None),
                "sort_key": 2,
                "mineral": "cassiterite",
                "description": f"Settlement Payment (Stock {getattr(s, 'voucher_no', '-')}) (Ref: {getattr(p, 'reference', None) or '-'})",
                "debit": 0.0,
                "credit": amt,
                "original_currency": original_currency,
                "original_amount": original_amount,
            })
        
        # Process unlinked settlements
        for p in cass_unlinked:
            amt = float(getattr(p, "amount_rwf", None) or getattr(p, "amount", 0.0) or 0.0)
            original_currency = (getattr(p, "currency", None) or "RWF").upper()
            original_amount = float(
                getattr(p, "input_amount", None)
                or getattr(p, "amount", 0.0)
                or getattr(p, "amount_rwf", 0.0)
                or 0.0
            )
            ledger_events.append({
                "date": getattr(p, "paid_at", None),
                "sort_key": 2,
                "mineral": "cassiterite",
                "description": f"Settlement Payment (All stocks) (Ref: {getattr(p, 'reference', None) or '-'})",
                "debit": 0.0,
                "credit": amt,
                "original_currency": original_currency,
                "original_amount": original_amount,
            })
    except Exception:
        pass

    # NOTE: Allocation entries (UnifiedSupplierAdvanceAllocation) are NOT added to ledger_events
    # because they represent internal tracking/bookkeeping, not actual transactions.
    # The allocations are preserved in the database for audit trail and wallet tracking,
    # but the ledger only shows actual financial transactions (advances, stocks, settlements).
    # This prevents double-counting: an advance is a real transaction (credit once),
    # and allocating it is just internal tracking of which advance covers which stock.

    # Unified advances and refunds (credits for advances, debits for refunds)
    for a in advances:
        amt = float(getattr(a, "amount_rwf", 0.0) or 0.0)
        original_currency = (getattr(a, "currency", None) or "RWF").upper()
        original_amount = float(
            getattr(a, "input_amount", None)
            or getattr(a, "amount_rwf", 0.0)
            or 0.0
        )
        source = (getattr(a, "source_mineral_type", None) or "").strip().lower()
        mineral = "coltan" if source in {"copper", "coltan"} else ("cassiterite" if source == "cassiterite" else ("refund" if source == "refund" else "-"))
        is_refund = bool(source == "refund" or amt < 0)
        description = "Supplier Refund" if is_refund else "Advance Payment"
        ref = getattr(a, "reference", None) or "-"
        note = getattr(a, "note", None) or ""
        desc = f"{description} (Ref: {ref})" + (f" - {note}" if note else "")

        ledger_events.append({
            "date": getattr(a, "paid_at", None),
            "sort_key": 0,
            "mineral": mineral,
            "description": desc,
            "debit": abs(amt) if is_refund else 0.0,
            "credit": 0.0 if is_refund else amt,
            "original_currency": original_currency,
            "original_amount": original_amount,
        })

    from datetime import date as _date

    def _ledger_sort_value(value):
        if isinstance(value, datetime):
            return value
        if isinstance(value, _date):
            return datetime.combine(value, time.min)
        return datetime.min

    ledger_events.sort(key=lambda item: (_ledger_sort_value(item.get("date")), item.get("sort_key", 0)))

    def _event_in_range(ev):
        ev_date = ev.get('date')
        if not ev_date:
            return False
        d = ev_date.date() if isinstance(ev_date, datetime) else ev_date
        if filter_from and d < filter_from:
            return False
        if filter_to and d > filter_to:
            return False
        return True

    def _event_matches_voucher(ev):
        if not voucher_q:
            return True
        desc = (ev.get('description') or '')
        return voucher_q.lower() in desc.lower()

    def _event_matches_mineral(ev):
        if not mineral_q or mineral_q in {'all', ''}:
            return True
        m = (ev.get('mineral') or '').strip().lower()
        if mineral_q in {'copper', 'coltan'}:
            return m == 'coltan'
        return m == mineral_q

    opening_balance = 0.0
    if filter_from:
        start_dt = datetime.combine(filter_from, time.min)
        try:
            from copper.models import CopperStock, SupplierPayment as CopperSupplierPayment
            from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment

            before_stock_debit = float(
                db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0))
                .filter(
                    CopperStock.is_deleted.is_(False),
                    func.lower(CopperStock.supplier).ilike(supplier_like),
                    CopperStock.date < filter_from,
                )
                .scalar()
                or 0.0
            )
            before_stock_debit += float(
                db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0))
                .filter(
                    CassiteriteStock.is_deleted.is_(False),
                    func.lower(CassiteriteStock.supplier).ilike(supplier_like),
                    CassiteriteStock.date < filter_from,
                )
                .scalar()
                or 0.0
            )

            before_settlement_credit = float(
                db.session.query(func.coalesce(func.sum(func.coalesce(CopperSupplierPayment.amount_rwf, CopperSupplierPayment.amount)), 0))
                .join(CopperStock, CopperStock.id == CopperSupplierPayment.stock_id)
                .filter(
                    CopperSupplierPayment.is_deleted.is_(False),
                    CopperSupplierPayment.stock_id.isnot(None),
                    CopperSupplierPayment.paid_at < start_dt,
                    CopperStock.is_deleted.is_(False),
                    func.lower(CopperStock.supplier).ilike(supplier_like),
                )
                .scalar()
                or 0.0
            )
            before_settlement_credit += float(
                db.session.query(func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0))
                .join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id)
                .filter(
                    CassiteriteSupplierPayment.is_deleted.is_(False),
                    CassiteriteSupplierPayment.stock_id.isnot(None),
                    CassiteriteSupplierPayment.paid_at < start_dt,
                    CassiteriteStock.is_deleted.is_(False),
                    func.lower(CassiteriteStock.supplier).ilike(supplier_like),
                )
                .scalar()
                or 0.0
            )

            before_alloc_credit = float(
                db.session.query(func.coalesce(func.sum(UnifiedSupplierAdvanceAllocation.applied_amount), 0))
                .join(UnifiedSupplierAdvance, UnifiedSupplierAdvance.id == UnifiedSupplierAdvanceAllocation.advance_id)
                .filter(
                    UnifiedSupplierAdvance.is_deleted.is_(False),
                    advance_supplier_filter,
                    UnifiedSupplierAdvanceAllocation.created_at < start_dt,
                )
                .scalar()
                or 0.0
            )

            before_adv_credit = float(
                db.session.query(func.coalesce(func.sum(UnifiedSupplierAdvance.amount_rwf), 0))
                .filter(
                    UnifiedSupplierAdvance.is_deleted.is_(False),
                    advance_supplier_filter,
                    UnifiedSupplierAdvance.paid_at < start_dt,
                    UnifiedSupplierAdvance.amount_rwf > 0,
                )
                .scalar()
                or 0.0
            )
            before_refund_debit = float(
                db.session.query(func.coalesce(func.sum(func.abs(UnifiedSupplierAdvance.amount_rwf)), 0))
                .filter(
                    UnifiedSupplierAdvance.is_deleted.is_(False),
                    advance_supplier_filter,
                    UnifiedSupplierAdvance.paid_at < start_dt,
                    UnifiedSupplierAdvance.amount_rwf < 0,
                )
                .scalar()
                or 0.0
            )

            # NOTE: before_alloc_credit is NOT included in opening_balance calculation anymore
            # because allocations are internal bookkeeping, not actual ledger transactions.
            # Opening balance = stocks (debit) + refunds (debit) - settlements (credit) - advances (credit)
            opening_balance = float(
                (before_stock_debit + before_refund_debit)
                - (before_settlement_credit + before_adv_credit)
            )
        except Exception:
            opening_balance = 0.0

    filtered_events: list[dict] = []
    for ev in ledger_events:
        if _event_in_range(ev) and _event_matches_voucher(ev) and _event_matches_mineral(ev):
            filtered_events.append(ev)

    ledger_entries: list[dict] = []
    period_debit = float(sum([float(ev.get('debit') or 0.0) for ev in filtered_events]) or 0.0)
    period_credit = float(sum([float(ev.get('credit') or 0.0) for ev in filtered_events]) or 0.0)
    period_net = float(period_debit - period_credit)

    if filter_from or filter_to or voucher_q or (mineral_q and mineral_q not in {'all', ''}):
        ledger_entries.append({
            'date': datetime.combine(filter_from or today, time.min) if (filter_from or filter_to) else None,
            'mineral': '-',
            'description': 'Opening Balance',
            'debit': 0.0,
            'credit': 0.0,
            'balance': float(opening_balance or 0.0),
            'original_currency': None,
            'original_amount': None,
        })

    running_filtered = float(opening_balance or 0.0)
    for ev in filtered_events:
        debit = float(ev.get('debit') or 0.0)
        credit = float(ev.get('credit') or 0.0)
        running_filtered += debit - credit
        ledger_entries.append({
            'date': ev.get('date'),
            'mineral': ev.get('mineral') or '-',
            'description': ev.get('description') or '-',
            'debit': debit,
            'credit': credit,
            'balance': running_filtered,
            'original_currency': ev.get('original_currency'),
            'original_amount': ev.get('original_amount'),
        })

    ledger_running_balance = float(ledger_entries[-1]['balance'] if ledger_entries else supplier_remaining)

    # ledger_entries remains the running sequence produced from events; do not
    # inject an authoritative synthetic row here — receipts must match the
    # ledger's running balance computed from the same event stream.

    from utils import generate_supplier_slug
    
    return render_template(
        "suppliers/consolidated_ledger.html",
        supplier_name=supplier_name,
        supplier_norm=norm,
        supplier_slug=supplier_slug or generate_supplier_slug(supplier_name or norm),
        wallet_remaining=wallet_remaining,
        total_advanced=total_advanced,
        total_refunded=total_refunded,
        supplier_remaining=supplier_remaining,
        ledger_running_balance=ledger_running_balance,
        allocation_rows=allocation_rows,
        advances=advances,
        copper_debt=copper_debt,
        cassiterite_debt=cassiterite_debt,
        ledger_entries=ledger_entries,
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
        filter_voucher=voucher_q,
        filter_mineral=mineral_q or 'all',
        period_debit=period_debit,
        period_credit=period_credit,
        period_net=period_net,
    )



@core_bp.route('/accountant/suppliers/charge_business_retention', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def charge_business_retention():
    if request.method == 'GET':
        from core.models import TransporterLedger
        transporters = [
            name for (name,) in (
                db.session.query(TransporterLedger.transporter_name)
                .distinct()
                .order_by(TransporterLedger.transporter_name.asc())
                .all()
            ) if name
        ]
        return render_template('suppliers/charge_business_retention.html', transporters=transporters)

    # POST: create SupplierDeduction and a linked transporter ledger recovery row.
    supplier_name = (request.form.get('supplier_name') or '').strip()
    transporter_name = (request.form.get('transporter_name') or '').strip()
    total_weight = float(request.form.get('total_weight') or 0.0)
    rate = float(request.form.get('rate') or 0.0)
    currency = (request.form.get('currency') or 'RWF').strip().upper()
    exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    note = (request.form.get('note') or '').strip() or None

    if not supplier_name:
        flash('Supplier name is required.', 'danger')
        return redirect(url_for('core.charge_business_retention'))
    if not transporter_name:
        flash('Transporter is required.', 'danger')
        return redirect(url_for('core.charge_business_retention'))
    if total_weight <= 0 or rate <= 0:
        flash('Total weight and rate must be greater than zero.', 'danger')
        return redirect(url_for('core.charge_business_retention'))

    try:
        amount_input = float(total_weight) * float(rate)
        amount_rwf = float(amount_input) * (float(exchange_rate) if currency == 'USD' else 1.0)

        from core.models import SupplierDeduction, TransporterLedger

        sd = SupplierDeduction(
            supplier_name=supplier_name,
            deduction_type='BUSINESS_RETENTION',
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate if currency == 'USD' else 1.0,
            amount_rwf=amount_rwf,
            created_by_id=getattr(current_user, 'id', None),
            note=note or f"Business retention fee for transporter {transporter_name}",
        )
        db.session.add(sd)
        db.session.flush()

        # This is a recovery from the transporter balance, so it reduces what we owe him.
        t = TransporterLedger(
            transporter_name=transporter_name,
            supplier_name=supplier_name,
            entry_type='BUSINESS_RETENTION_RECOVERY',
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate if currency == 'USD' else 1.0,
            amount_rwf=float(-abs(amount_rwf)),
            created_by_id=getattr(current_user, 'id', None),
            note=f"Business retention consumed from supplier {supplier_name}: " + (note or ''),
            source_supplier_deduction_id=int(sd.id),
        )
        db.session.add(t)

        db.session.commit()
        flash(f'Fee recorded: {amount_input:,.2f} {currency} ({amount_rwf:,.2f} RWF) for {supplier_name}.', 'success')
        return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier_name))
    except Exception:
        db.session.rollback()
        logger.exception('charge_business_retention: failed to record fee')
        flash('Failed to record fee, see logs.', 'danger')
        return redirect(url_for('core.charge_business_retention'))


@core_bp.route('/accountant/suppliers/supplier_payment', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def supplier_payment():
    from core.models import PaymentReview, PaymentReviewStatus

    if request.method == 'GET':
        from core.models import TransporterLedger
        transporters = [
            name for (name,) in (
                db.session.query(TransporterLedger.transporter_name)
                .distinct()
                .order_by(TransporterLedger.transporter_name.asc())
                .all()
            ) if name
        ]
        return render_template('suppliers/supplier_payment.html', transporters=transporters)

    # POST: create PaymentReview for boss approval
    supplier_name = (request.form.get('supplier_name') or '').strip()
    transporter_name = (request.form.get('transporter_name') or '').strip()
    total_weight = float(request.form.get('total_weight') or 0.0)
    rate = float(request.form.get('rate') or 0.0)
    currency = (request.form.get('currency') or 'RWF').strip().upper()
    exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    note = (request.form.get('note') or '').strip() or None

    if not supplier_name:
        flash('Supplier name is required.', 'danger')
        return redirect(url_for('core.supplier_payment'))
    if not transporter_name:
        flash('Transporter is required.', 'danger')
        return redirect(url_for('core.supplier_payment'))
    if total_weight <= 0 or rate <= 0:
        flash('Total weight and rate must be greater than zero.', 'danger')
        return redirect(url_for('core.supplier_payment'))

    try:
        amount_input = float(total_weight) * float(rate)
        amount_rwf = float(amount_input) * (float(exchange_rate) if currency == 'USD' else 1.0)

        payload = {
            'action': 'collect_supplier_payment',
            'supplier_name': supplier_name,
            'transporter_name': transporter_name,
            'total_weight': total_weight,
            'rate': rate,
            'amount_input': amount_input,
            'currency': currency,
            'exchange_rate': exchange_rate if currency == 'USD' else 1.0,
            'amount_rwf': amount_rwf,
            'note': 'UTANGA UMUSARURO ARATWISHYUYE AJYANYE UMUSARURO WE',
        }

        review = PaymentReview(
            mineral_type=None,
            type='cash_collect_supplier_payment',
            customer=f"{supplier_name} (via {transporter_name})",
            amount=amount_input,
            currency=currency,
            created_by_id=getattr(current_user, 'id', None),
            status=PaymentReviewStatus.PENDING_REVIEW.value,
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.commit()

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='SUPPLIER_PAYMENT_REQUEST',
                message=f'Supplier payment request created for {supplier_name} (transporter: {transporter_name}): {amount_input:,.2f} {currency}.',
                related_type='payment_review',
                related_id=int(review.id),
            )

        flash(f'Supplier payment request submitted for boss approval: {amount_input:,.2f} {currency} (~{amount_rwf:,.0f} RWF).', 'success')
        return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier_name))
    except Exception:
        db.session.rollback()
        logger.exception('supplier_payment: failed to create payment request')
        flash('Failed to create payment request, see logs.', 'danger')
        return redirect(url_for('core.supplier_payment'))


@core_bp.route('/accountant/transporter-ledger', methods=['GET'])
@role_required('accountant', 'boss', 'admin')
def transporter_ledger_index():
    from sqlalchemy import func
    from core.models import TransporterLedger

    preset = (request.args.get('preset') or '30d').strip().lower()
    filter_preset, filter_from, filter_to = _transporter_ledger_date_window(preset)
    try:
        page = int(request.args.get('page') or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)
    try:
        per_page = int(request.args.get('per_page') or 12)
    except (TypeError, ValueError):
        per_page = 12
    per_page = min(max(per_page, 1), 50)

    rows_query = (
        db.session.query(
            TransporterLedger.transporter_name,
            func.coalesce(func.sum(TransporterLedger.amount_rwf), 0).label('balance_rwf'),
            func.count(TransporterLedger.id).label('row_count'),
        )
        .group_by(TransporterLedger.transporter_name)
        .order_by(TransporterLedger.transporter_name.asc())
    )
    rows_pagination = rows_query.paginate(page=page, per_page=per_page, error_out=False)
    rows = rows_pagination.items

    recent = (
        TransporterLedger.query
        .filter(
            *(
                [TransporterLedger.created_at >= datetime.combine(filter_from, datetime.min.time())] if filter_from else []
            ),
            *(
                [TransporterLedger.created_at <= datetime.combine(filter_to, datetime.max.time())] if filter_to else []
            ),
        )
        .order_by(TransporterLedger.created_at.desc(), TransporterLedger.id.desc())
        .limit(100)
        .all()
    )
    recent_rows = _build_transporter_ledger_rows(recent)
    payment_history_source = (
        TransporterLedger.query
        .filter(TransporterLedger.is_paid.is_(True))
        .filter(TransporterLedger.entry_type.in_(['ADVANCE', 'CASH_PAYMENT', 'TRANSPORTER_FEE_CHARGE']))
        .order_by(TransporterLedger.created_at.desc(), TransporterLedger.id.desc())
        .limit(12)
        .all()
    )
    payment_history_rows = _build_transporter_ledger_rows(payment_history_source)
    transporter_names = [
        name for (name,) in (
            db.session.query(TransporterLedger.transporter_name)
            .distinct()
            .order_by(TransporterLedger.transporter_name.asc())
            .all()
        ) if name
    ]
    return render_template(
        'suppliers/transporter_ledger.html',
        rows=rows,
        rows_pagination=rows_pagination,
        recent_rows=recent_rows,
        payment_history_rows=payment_history_rows,
        transporter_names=transporter_names,
        filter_preset=filter_preset,
        ledger_url_base=url_for('core.transporter_ledger_index'),
        page_size=per_page,
    )


def _get_transporter_names():
    from core.models import TransporterLedger

    return [
        name for (name,) in (
            db.session.query(TransporterLedger.transporter_name)
            .distinct()
            .order_by(TransporterLedger.transporter_name.asc())
            .all()
        ) if name
    ]


def _render_transporter_fee_form(mode: str, transporter_name: str = '', amount_input: float = 0.0, currency: str = 'RWF', exchange_rate: float = 1.0, note: str = ''):
    return render_template(
        'suppliers/transporter_fee_form.html',
        mode=mode,
        transporter_names=_get_transporter_names(),
        transporter_name=transporter_name,
        amount_input=amount_input,
        currency=currency,
        exchange_rate=exchange_rate,
        note=note,
    )


@core_bp.route('/accountant/transporter-ledger/pay-fee', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def transporter_pay_fee():
    from core.models import PaymentReview, PaymentReviewStatus, TransporterLedger

    if request.method == 'GET':
        return _render_transporter_fee_form('pay')

    transporter_name = (request.form.get('transporter_name') or '').strip()
    currency = (request.form.get('currency') or 'RWF').strip().upper()
    note = (request.form.get('note') or '').strip() or None
    entry_kind = (request.form.get('entry_kind') or 'ADVANCE').strip().upper()
    try:
        amount_input = float(request.form.get('amount_input') or 0.0)
    except Exception:
        amount_input = 0.0
    try:
        exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    except Exception:
        exchange_rate = 1.0

    if not transporter_name:
        flash('Transporter name is required.', 'danger')
        return _render_transporter_fee_form('pay', amount_input=amount_input, currency=currency, exchange_rate=exchange_rate, note=note or '')
    if amount_input <= 0:
        flash('Amount must be greater than zero.', 'danger')
        return _render_transporter_fee_form('pay', transporter_name=transporter_name, currency=currency, exchange_rate=exchange_rate, note=note or '')
    if entry_kind not in {'ADVANCE', 'CASH_PAYMENT'}:
        entry_kind = 'ADVANCE'

    existing_names = _get_transporter_names()
    close_matches = close_name_matches(transporter_name, existing_names, limit=5, cutoff=0.86)
    if close_matches and transporter_name not in existing_names:
        flash(f"Did you mean: {', '.join(close_matches)}? Use the exact transporter name to avoid duplicate ledgers.", 'info')

    amount_rwf = float(amount_input) * (exchange_rate if currency == 'USD' else 1.0)
    
    # Determine default note based on entry_kind
    if not note:
        if entry_kind == 'ADVANCE':
            note = f'Transporter advance for {transporter_name}'
        else:
            note = f'Transporter transport fees for {transporter_name}'
    
    payload = {
        'action': 'pay_transporter',
        'entry_kind': entry_kind,
        'transporter_name': transporter_name,
        'amount_input': amount_input,
        'currency': currency,
        'exchange_rate': exchange_rate if currency == 'USD' else 1.0,
        'amount_rwf': amount_rwf,
        'note': note,
    }

    review = PaymentReview(
        mineral_type=None,
        type='transporter_advance' if entry_kind == 'ADVANCE' else 'transporter_payment',
        customer=transporter_name,
        amount=amount_input,
        currency=currency,
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
        request_payload=json.dumps(payload),
    )
    db.session.add(review)
    db.session.commit()

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        notification_type = 'TRANSPORTER_ADVANCE_REQUEST' if entry_kind == 'ADVANCE' else 'TRANSPORTER_PAYMENT_REQUEST'
        message_text = f'Advance request created for transporter {transporter_name}: {amount_input:,.2f} {currency}.' if entry_kind == 'ADVANCE' else f'Transport fees payment request created for {transporter_name}: {amount_input:,.2f} {currency}.'
        create_notification(
            user_id=int(boss_id),
            type_=notification_type,
            message=message_text,
            related_type='payment_review',
            related_id=int(review.id),
        )

    kind_label = 'Advance' if entry_kind == 'ADVANCE' else 'Transport fees'
    flash(f'Transporter {kind_label} request submitted for boss approval: {amount_input:,.2f} {currency} (~{amount_rwf:,.0f} RWF).', 'success')
    return redirect(url_for('core.transporter_ledger_detail', transporter_name=transporter_name))


@core_bp.route('/accountant/transporter-ledger/request-advance', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def transporter_request_advance():
    return transporter_pay_fee()


@core_bp.route('/accountant/transporter-ledger/<path:transporter_name>', methods=['GET'])
@role_required('accountant', 'boss', 'admin')
def transporter_ledger_detail(transporter_name: str):
    from sqlalchemy import func
    from core.models import TransporterLedger

    normalized = ' '.join((transporter_name or '').strip().lower().split())
    if not normalized:
        flash('Transporter name is required.', 'danger')
        return redirect(url_for('core.transporter_ledger_index'))

    preset = (request.args.get('preset') or '30d').strip().lower()
    filter_preset, filter_from, filter_to = _transporter_ledger_date_window(preset)

    rows = (
        db.session.query(
            TransporterLedger.transporter_name,
            func.coalesce(func.sum(TransporterLedger.amount_rwf), 0).label('balance_rwf'),
            func.count(TransporterLedger.id).label('row_count'),
        )
        .filter(func.lower(func.trim(TransporterLedger.transporter_name)) == normalized)
        .group_by(TransporterLedger.transporter_name)
        .order_by(TransporterLedger.transporter_name.asc())
        .all()
    )

    recent = (
        TransporterLedger.query
        .filter(func.lower(func.trim(TransporterLedger.transporter_name)) == normalized)
        .filter(
            *(
                [TransporterLedger.created_at >= datetime.combine(filter_from, datetime.min.time())] if filter_from else []
            ),
            *(
                [TransporterLedger.created_at <= datetime.combine(filter_to, datetime.max.time())] if filter_to else []
            ),
        )
        .order_by(TransporterLedger.created_at.desc(), TransporterLedger.id.desc())
        .all()
    )
    
    # Calculate opening balance (sum of all entries BEFORE filter period)
    opening_balance = 0.0
    if filter_from:
        opening_balance = float(
            db.session.query(func.coalesce(func.sum(TransporterLedger.amount_rwf), 0.0))
            .filter(func.lower(func.trim(TransporterLedger.transporter_name)) == normalized)
            .filter(TransporterLedger.created_at < datetime.combine(filter_from, datetime.min.time()))
            .scalar() or 0.0
        )
    
    recent_rows = _build_transporter_ledger_rows(recent, opening_balance=opening_balance if filter_from else None)
    transporter_names = [
        name for (name,) in (
            db.session.query(TransporterLedger.transporter_name)
            .distinct()
            .order_by(TransporterLedger.transporter_name.asc())
            .all()
        ) if name
    ]
    display_name = rows[0].transporter_name if rows else transporter_name
    return render_template(
        'suppliers/transporter_ledger.html',
        rows=rows,
        recent_rows=recent_rows,
        transporter_names=transporter_names,
        selected_transporter=display_name,
        filter_preset=filter_preset,
        ledger_url_base=url_for('core.transporter_ledger_detail', transporter_name=display_name),
        opening_balance=opening_balance if filter_from else None,
    )


@core_bp.route('/accountant/transporter-ledger/charge-fee', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def transporter_charge_fee():
    from core.models import PaymentReview, PaymentReviewStatus

    if request.method == 'GET':
        return _render_transporter_fee_form('charge')

    transporter_name = (request.form.get('transporter_name') or '').strip()
    currency = (request.form.get('currency') or 'RWF').strip().upper()
    note = (request.form.get('note') or '').strip() or None
    try:
        amount_input = float(request.form.get('amount_input') or 0.0)
    except Exception:
        amount_input = 0.0
    try:
        exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    except Exception:
        exchange_rate = 1.0

    if not transporter_name:
        flash('Transporter name is required.', 'danger')
        return _render_transporter_fee_form('charge', amount_input=amount_input, currency=currency, exchange_rate=exchange_rate, note=note or '')
    if amount_input <= 0:
        flash('Charge amount must be greater than zero.', 'danger')
        return _render_transporter_fee_form('charge', transporter_name=transporter_name, currency=currency, exchange_rate=exchange_rate, note=note or '')
    if not note:
        flash('Please explain why this transporter is being charged.', 'danger')
        return _render_transporter_fee_form('charge', transporter_name=transporter_name, amount_input=amount_input, currency=currency, exchange_rate=exchange_rate, note='')

    amount_rwf = float(amount_input) * (exchange_rate if currency == 'USD' else 1.0)
    payload = {
        'action': 'charge_transporter_fee',
        'entry_kind': 'TRANSPORTER_FEE_CHARGE',
        'transporter_name': transporter_name,
        'amount_input': amount_input,
        'currency': currency,
        'exchange_rate': exchange_rate if currency == 'USD' else 1.0,
        'amount_rwf': amount_rwf,
        'note': note,
    }

    review = PaymentReview(
        mineral_type=None,
        type='transporter_fee_charge',
        customer=transporter_name,
        amount=amount_input,
        currency=currency,
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
        request_payload=json.dumps(payload),
    )
    db.session.add(review)
    db.session.commit()

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        create_notification(
            user_id=int(boss_id),
            type_='TRANSPORTER_FEE_CHARGE_REQUEST',
            message=f'Transporter fee charge review created for {transporter_name}: {amount_input:,.2f} {currency}.',
            related_type='payment_review',
            related_id=int(review.id),
        )

    flash(f'Transporter fee charge review submitted for boss approval: {amount_input:,.2f} {currency} (~{amount_rwf:,.0f} RWF). This will update the transporter ledger only after approval.', 'success')
    return redirect(url_for('core.transporter_ledger_detail', transporter_name=transporter_name))


@core_bp.route('/accountant/transporter-ledger/historical-record', methods=['GET', 'POST'])
@role_required('accountant', 'boss', 'admin')
def record_historical_transporter_advance():
    """
    Temporary workflow to record historical transporter advances directly into the ledger
    without going through boss approval or cash disbursement.
    This bypasses the normal workflow and directly creates TransporterLedger entries.
    """
    from core.models import TransporterLedger

    if request.method == 'GET':
        return render_template(
            'suppliers/historical_transporter_advance.html',
            transporter_names=_get_transporter_names(),
        )

    # POST: Record the historical advance
    transporter_name = (request.form.get('transporter_name') or '').strip()
    currency = (request.form.get('currency') or 'RWF').strip().upper()
    note = (request.form.get('note') or '').strip() or None
    recorded_at_str = (request.form.get('recorded_at') or '').strip()
    
    try:
        amount_input = float(request.form.get('amount_input') or 0.0)
    except Exception:
        amount_input = 0.0
    try:
        exchange_rate = float(request.form.get('exchange_rate') or 1.0)
    except Exception:
        exchange_rate = 1.0

    if not transporter_name:
        flash('Transporter name is required.', 'danger')
        return render_template(
            'suppliers/historical_transporter_advance.html',
            transporter_names=_get_transporter_names(),
            transporter_name=transporter_name,
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate,
            note=note or '',
        )
    if amount_input <= 0:
        flash('Amount must be greater than zero.', 'danger')
        return render_template(
            'suppliers/historical_transporter_advance.html',
            transporter_names=_get_transporter_names(),
            transporter_name=transporter_name,
            currency=currency,
            exchange_rate=exchange_rate,
            note=note or '',
        )

    # Parse the recorded date if provided
    recorded_at = None
    if recorded_at_str:
        try:
            recorded_at = datetime.fromisoformat(recorded_at_str)
        except Exception:
            flash('Invalid date format. Using current time.', 'warning')
            recorded_at = datetime.utcnow()
    else:
        recorded_at = datetime.utcnow()

    amount_rwf = float(amount_input) * (exchange_rate if currency == 'USD' else 1.0)

    try:
        # Directly create a TransporterLedger entry with no approval workflow
        ledger = TransporterLedger(
            transporter_name=transporter_name,
            supplier_name=None,
            entry_type='ADVANCE',
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate if currency == 'USD' else 1.0,
            amount_rwf=float(amount_rwf),
            is_paid=True,
            paid_at=recorded_at,
            created_by_id=getattr(current_user, 'id', None),
            created_at=recorded_at,
            note=note or f'Historical transporter advance (recorded {recorded_at.strftime("%Y-%m-%d")})',
        )
        db.session.add(ledger)
        db.session.commit()

        flash(
            f'Historical advance recorded directly: {amount_input:,.2f} {currency} (~{amount_rwf:,.0f} RWF) for {transporter_name} on {recorded_at.strftime("%Y-%m-%d")}.',
            'success'
        )
        return redirect(url_for('core.transporter_ledger_detail', transporter_name=transporter_name))
    except Exception as e:
        db.session.rollback()
        logger.exception('Failed to record historical transporter advance')
        flash(f'Failed to record historical advance: {str(e)}', 'danger')
        return render_template(
            'suppliers/historical_transporter_advance.html',
            transporter_names=_get_transporter_names(),
            transporter_name=transporter_name,
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate,
            note=note or '',
            recorded_at=recorded_at_str,
        )


@core_bp.route('/api/transporters/autocomplete')
@role_required('accountant', 'boss', 'admin', 'cashier', 'negotiator')
def transporters_autocomplete():
    from core.models import TransporterLedger

    q = (request.args.get('q') or '').strip()
    if not q:
        return {'results': []}
    q_norm = normalize_counterparty_name(q)
    rows = (
        db.session.query(TransporterLedger.transporter_name)
        .filter(func.lower(func.trim(TransporterLedger.transporter_name)).contains(q_norm))
        .distinct()
        .order_by(TransporterLedger.transporter_name.asc())
        .limit(15)
        .all()
    )
    return {'results': [nm for (nm,) in rows if nm]}


@core_bp.route('/accountant/transporter-ledger/<path:transporter_name>/request-payment', methods=['POST'])
@role_required('accountant', 'boss', 'admin')
def transporter_request_payment(transporter_name: str):
    from sqlalchemy import func
    from core.models import TransporterLedger, PaymentReview, PaymentReviewStatus

    normalized = ' '.join((transporter_name or '').strip().lower().split())
    if not normalized:
        flash('Transporter name is required.', 'danger')
        return redirect(url_for('core.transporter_ledger_index'))

    balance_rwf = float(
        db.session.query(func.coalesce(func.sum(TransporterLedger.amount_rwf), 0))
        .filter(func.lower(func.trim(TransporterLedger.transporter_name)) == normalized)
        .scalar()
        or 0.0
    )
    if balance_rwf <= 0:
        flash('This transporter has no positive balance to pay.', 'info')
        return redirect(url_for('core.transporter_ledger_index'))

    existing = (
        PaymentReview.query
        .filter(
            PaymentReview.type == 'transporter_payment',
            PaymentReview.status.in_([PaymentReviewStatus.PENDING_REVIEW.value, PaymentReviewStatus.APPROVED.value]),
            PaymentReview.request_payload.ilike(f'%"transporter_name": "{normalized}"%'),
        )
        .order_by(PaymentReview.id.desc())
        .first()
    )
    if existing:
        flash('A transporter payment review already exists for this transporter.', 'info')
        return redirect(url_for('core.transporter_ledger_index'))

    payload = {
        'action': 'pay_transporter',
        'transporter_name': normalized,
        'amount_rwf': balance_rwf,
        'amount_input': balance_rwf,
        'currency': 'RWF',
        'exchange_rate': 1.0,
        'note': f'Transporter settlement for {transporter_name}',
    }
    review = PaymentReview(
        mineral_type=None,
        type='transporter_payment',
        customer=transporter_name,
        amount=balance_rwf,
        currency='RWF',
        created_by_id=getattr(current_user, 'id', None),
        status=PaymentReviewStatus.PENDING_REVIEW.value,
        request_payload=json.dumps(payload),
    )
    db.session.add(review)
    db.session.commit()

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        create_notification(
            user_id=int(boss_id),
            type_='TRANSPORTER_PAYMENT_REQUEST',
            message=f'Payment request created for transporter {transporter_name}: {balance_rwf:,.2f} RWF.',
            related_type='payment_review',
            related_id=int(review.id),
        )

    flash('Transporter payment review submitted for boss approval.', 'success')
    return redirect(url_for('core.transporter_ledger_detail', transporter_name=transporter_name))


def _render_transporter_receipt_detail(ledger_id: int):
    from core.models import TransporterLedger

    row = TransporterLedger.query.get_or_404(ledger_id)
    transporter_name = row.transporter_name or 'Unknown'
    entry_type = (row.entry_type or '').strip().upper()
    current_balance = (
        db.session.query(func.coalesce(func.sum(TransporterLedger.amount_rwf), 0.0))
        .filter(func.lower(func.trim(TransporterLedger.transporter_name)) == func.lower(func.trim(transporter_name)))
        .filter(TransporterLedger.id <= int(row.id))
        .scalar()
        or 0.0
    )

    is_advance = entry_type == 'ADVANCE'
    is_fee_charge = entry_type in {'TRANSPORTER_FEE_CHARGE', 'BUSINESS_RETENTION_RECOVERY'}
    if is_advance:
        receipt_reference = f'TRP-ADV-{row.id:04d}'
        document_title = 'TRANSPORTER ADVANCE RECEIPT'
        document_subtitle = 'Transporter advance receipt with approval signatures'
        status_label = 'ADVANCE PAID'
    elif is_fee_charge:
        receipt_reference = f'TRP-FEE-{row.id:04d}'
        document_title = 'TRANSPORTER FEE CHARGE RECEIPT'
        document_subtitle = 'Transporter fee charge receipt with approval signatures'
        status_label = 'FEE CHARGED'
    else:
        receipt_reference = f'TRP-PAY-{row.id:04d}'
        document_title = 'TRANSPORTER PAYMENT RECEIPT'
        document_subtitle = 'Transporter disbursement receipt with approval signatures'
        status_label = 'PAYMENT DISBURSED'

    return render_template(
        'receipts/professional_payment_receipt.html',
        document_title=document_title,
        document_subtitle=document_subtitle,
        party_role='Transporter',
        party_name=transporter_name,
        receipt_reference=receipt_reference,
        document_date=row.paid_at or row.created_at,
        original_amount=row.amount_input,
        original_currency=row.currency,
        exchange_rate=row.exchange_rate,
        paid_amount=row.amount_input,
        paid_currency=row.currency,
        paid_amount_rwf=row.amount_rwf,
        remaining_amount=current_balance,
        remaining_currency='RWF',
        note=row.note,
        status_label=status_label,
        signers=['Transporter', 'Cashier', 'Accountant', 'Boss'],
        summary_values={
            'balance_rwf': float(current_balance or 0.0),
            'amount_rwf': float(row.amount_rwf or 0.0),
        },
        hide_balance_rows=bool(is_advance or is_fee_charge or float(row.amount_rwf or 0.0) < 0.0),
    )


@core_bp.route('/receipts/transporter-payment/<int:ledger_id>', methods=['GET'])
@role_required('accountant', 'boss', 'cashier', 'admin')
def transporter_payment_receipt_detail(ledger_id: int):
    return _render_transporter_receipt_detail(ledger_id)


@core_bp.route('/receipts/transporter-advance/<int:ledger_id>', methods=['GET'])
@role_required('accountant', 'boss', 'cashier', 'admin')
def transporter_advance_receipt_detail(ledger_id: int):
    return _render_transporter_receipt_detail(ledger_id)


@core_bp.route('/accountant/suppliers/<path:supplier_norm>/settlement-statement', methods=['GET'])
@role_required('accountant', 'boss', 'admin', 'cashier')
def supplier_settlement_statement(supplier_norm: str):
    from core.models import UnifiedSupplierAdvance
    from utils import sql_normalize_counterparty_expr
    
    # Resolve supplier name from parameter (could be slug, normalized name, or direct name)
    input_norm = (supplier_norm or '').strip().lower()
    if not input_norm:
        abort(404)
    
    norm = None
    was_slug_input = False
    
    # Strategy 1: If input looks like a slug (contains hyphens, no spaces/slashes),
    # try to find the actual supplier name by matching the supplier_slug in DB
    if '-' in input_norm and ' ' not in input_norm and '/' not in input_norm:
        was_slug_input = True
        slug_matches = (
            db.session.query(UnifiedSupplierAdvance.supplier_name_norm)
            .filter(
                UnifiedSupplierAdvance.is_deleted.is_(False),
                UnifiedSupplierAdvance.supplier_slug == input_norm,
            )
            .group_by(UnifiedSupplierAdvance.supplier_name_norm)
            .first()
        )
        if slug_matches:
            norm = slug_matches[0]
    
    # Strategy 2: If no match yet, try treating input as a normalized name
    # OR if it was a slug, convert it to a spaced name for stock-only suppliers
    if not norm:
        if was_slug_input:
            # Convert slug "name-with-hyphens" to "name with hyphens" for stock lookups
            norm = ' '.join(input_norm.split('-'))
        else:
            # Already a normalized name
            norm = ' '.join(input_norm.split())
    
    if not norm:
        abort(404)

    supplier_like = f"%{'%'.join(norm.split())}%"

    reference = (request.args.get('reference') or '').strip()
    statement_date = datetime.utcnow().date()

    from copper.models import CopperStock
    from cassiterite.models import CassiteriteStock

    supplier_name = None
    try:
        supplier_name = (
            db.session.query(func.max(func.trim(CopperStock.supplier)))
            .filter(CopperStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like))
            .scalar()
        )
    except Exception:
        supplier_name = None
    if not supplier_name:
        try:
            supplier_name = (
                db.session.query(func.max(func.trim(CassiteriteStock.supplier)))
                .filter(CassiteriteStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like))
                .scalar()
            )
        except Exception:
            supplier_name = None
    supplier_name = supplier_name or supplier_norm

    rows = []

    copper_rows = (
        CopperStock.query
        .filter(CopperStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like))
        .order_by(CopperStock.date.desc(), CopperStock.id.desc())
        .limit(2000)
        .all()
    )
    for s in copper_rows:
        try:
            outstanding = float(s.remaining_to_pay() or 0.0)
        except Exception:
            outstanding = 0.0
        if outstanding <= 0:
            continue

        gross = float(getattr(s, 'amount', 0.0) or 0.0)
        transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
        rma = float(getattr(s, 'rma', 0.0) or 0.0)
        inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
        rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
        net = float(getattr(s, 'net_balance', 0.0) or 0.0)
        rows.append({
            'date': getattr(s, 'date', None),
            'mineral': 'Coltan',
            'voucher_no': getattr(s, 'voucher_no', None) or f"stock:{getattr(s, 'id', '')}",
            'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
            'gross': gross,
            'transport': transport,
            'rma': rma,
            'inkomane': inkomane,
            'rra_3_percent': rra,
            'net': net,
            'outstanding': outstanding,
        })

    cass_rows = (
        CassiteriteStock.query
        .filter(CassiteriteStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like))
        .order_by(CassiteriteStock.date.desc(), CassiteriteStock.id.desc())
        .limit(2000)
        .all()
    )
    for s in cass_rows:
        try:
            outstanding = float(s.remaining_to_pay() or 0.0)
        except Exception:
            outstanding = 0.0
        if outstanding <= 0:
            continue

        gross = float(getattr(s, 'amount_with_taxes', None) or getattr(s, 'amount', 0.0) or 0.0)
        transport = float(getattr(s, 'tot_amount_tag', 0.0) or 0.0)
        rma = float(getattr(s, 'rma', 0.0) or 0.0)
        inkomane = float(getattr(s, 'inkomane', 0.0) or 0.0)
        rra = float(getattr(s, 'rra_3_percent', 0.0) or 0.0)
        net = float(getattr(s, 'balance_to_pay', None) or getattr(s, 'net_balance', 0.0) or 0.0)
        rows.append({
            'date': getattr(s, 'date', None),
            'mineral': 'Cassiterite',
            'voucher_no': getattr(s, 'voucher_no', None) or f"stock:{getattr(s, 'id', '')}",
            'input_kg': float(getattr(s, 'input_kg', 0.0) or 0.0),
            'gross': gross,
            'transport': transport,
            'rma': rma,
            'inkomane': inkomane,
            'rra_3_percent': rra,
            'net': net,
            'outstanding': outstanding,
        })

    rows.sort(key=lambda r: (r.get('date') or datetime.min.date(), r.get('voucher_no') or ''), reverse=True)

    summary_gross = float(sum([float(r.get('gross') or 0.0) for r in rows]) or 0.0)
    summary_transport = float(sum([float(r.get('transport') or 0.0) for r in rows]) or 0.0)
    summary_rma = float(sum([float(r.get('rma') or 0.0) for r in rows]) or 0.0)
    summary_inkomane = float(sum([float(r.get('inkomane') or 0.0) for r in rows]) or 0.0)
    summary_rra = float(sum([float(r.get('rra_3_percent') or 0.0) for r in rows]) or 0.0)
    summary_net = float(sum([float(r.get('net') or 0.0) for r in rows]) or 0.0)
    summary_outstanding = float(sum([float(r.get('outstanding') or 0.0) for r in rows]) or 0.0)
    summary_deductions = float(summary_transport + summary_rma + summary_inkomane + summary_rra)

    summary = {
        'gross': summary_gross,
        'transport': summary_transport,
        'rma': summary_rma,
        'inkomane': summary_inkomane,
        'rra_3_percent': summary_rra,
        'deductions': summary_deductions,
        'net': summary_net,
        'outstanding': summary_outstanding,
    }

    from utils import generate_supplier_slug
    
    return render_template(
        'receipts/supplier_settlement_statement.html',
        supplier_name=supplier_name,
        supplier_norm=norm,
        supplier_slug=generate_supplier_slug(supplier_name or norm),
        reference=reference,
        statement_date=statement_date,
        rows=rows,
        summary=summary,
    )


@core_bp.route('/accountant/suppliers/<path:supplier_norm>/settle-open-balances', methods=['POST'])
@role_required('accountant', 'boss', 'admin')
def settle_supplier_open_balances(supplier_norm: str):
    from utils import normalize_counterparty_name, generate_supplier_slug, sql_normalize_counterparty_expr
    from copper.models import CopperStock, SupplierPayment as CopperSupplierPayment
    from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment
    from core.models import User

    input_norm = (supplier_norm or '').strip().lower()
    if not input_norm:
        abort(404)

    norm = None
    was_slug_input = False
    if '-' in input_norm and ' ' not in input_norm and '/' not in input_norm:
        was_slug_input = True
        slug_matches = (
            db.session.query(func.max(func.trim(CopperStock.supplier)))
            .filter(CopperStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CopperStock.supplier).ilike(f"%{'%'.join(input_norm.split('-'))}%"))
            .scalar()
        )
        if slug_matches:
            norm = normalize_counterparty_name(slug_matches)

    if not norm:
        candidate = ' '.join(input_norm.split('-')) if was_slug_input else input_norm
        norm = normalize_counterparty_name(candidate)

    if not norm:
        abort(404)

    supplier_like = f"%{'%'.join(norm.split())}%"
    supplier_name = None
    try:
        supplier_name = (
            db.session.query(func.max(func.trim(CopperStock.supplier)))
            .filter(CopperStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like))
            .scalar()
        )
    except Exception:
        supplier_name = None
    if not supplier_name:
        try:
            supplier_name = (
                db.session.query(func.max(func.trim(CassiteriteStock.supplier)))
                .filter(CassiteriteStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like))
                .scalar()
            )
        except Exception:
            supplier_name = None
    supplier_name = supplier_name or norm

    reference_base = (request.form.get('reference') or '').strip()
    audit_note = (request.form.get('note') or '').strip()
    if not reference_base:
        reference_base = f"AUTO-SETTLE-{generate_supplier_slug(supplier_name or norm) or norm}"
    now = datetime.utcnow()

    created_rows = 0
    total_rwf = 0.0

    # Copper stocks
    copper_stocks = (
        CopperStock.query
        .filter(CopperStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CopperStock.supplier).ilike(supplier_like))
        .order_by(CopperStock.date.asc(), CopperStock.id.asc())
        .all()
    )
    for stock in copper_stocks:
        try:
            outstanding = float(stock.remaining_to_pay() or 0.0)
        except Exception:
            outstanding = 0.0
        if outstanding <= 0:
            continue
        payment = CopperSupplierPayment(
            supplier_name=(stock.supplier or supplier_name or norm),
            stock_id=stock.id,
            amount=outstanding,
            input_amount=outstanding,
            currency='RWF',
            exchange_rate=1.0,
            amount_rwf=outstanding,
            paid_at=now,
            method='system_settlement',
            reference=f"{reference_base}-{getattr(stock, 'voucher_no', stock.id)}",
            note=audit_note or 'Direct supplier settlement of open balance',
            payment_type='SETTLEMENT',
            approval_status='APPROVED',
            disbursement_status='DISBURSED',
            created_by_id=getattr(current_user, 'id', None),
            is_advance=False,
        )
        db.session.add(payment)
        created_rows += 1
        total_rwf += outstanding

    # Cassiterite stocks
    cass_stocks = (
        CassiteriteStock.query
        .filter(CassiteriteStock.is_deleted.is_(False), sql_normalize_counterparty_expr(CassiteriteStock.supplier).ilike(supplier_like))
        .order_by(CassiteriteStock.date.asc(), CassiteriteStock.id.asc())
        .all()
    )
    for stock in cass_stocks:
        try:
            outstanding = float(stock.remaining_to_pay() or 0.0)
        except Exception:
            outstanding = 0.0
        if outstanding <= 0:
            continue
        payment = CassiteriteSupplierPayment(
            supplier_name=(stock.supplier or supplier_name or norm),
            stock_id=stock.id,
            amount=outstanding,
            input_amount=outstanding,
            currency='RWF',
            exchange_rate=1.0,
            amount_rwf=outstanding,
            paid_at=now,
            method='system_settlement',
            reference=f"{reference_base}-{getattr(stock, 'voucher_no', stock.id)}",
            note=audit_note or 'Direct supplier settlement of open balance',
            payment_type='SETTLEMENT',
            approval_status='APPROVED',
            disbursement_status='DISBURSED',
            created_by_id=getattr(current_user, 'id', None),
            is_advance=False,
        )
        db.session.add(payment)
        created_rows += 1
        total_rwf += outstanding

    if created_rows <= 0:
        flash('No open balances found for this supplier.', 'info')
        return redirect(url_for('core.consolidated_supplier_ledger', supplier_norm=supplier_norm))

    db.session.commit()
    flash(f'Settled {created_rows} stock balance(s) for {supplier_name} totaling {total_rwf:,.2f} RWF.', 'success')
    return redirect(url_for('core.consolidated_supplier_ledger', supplier_norm=supplier_norm))




@core_bp.route("/boss/cash/reconciliations", methods=["GET"])
@role_required("boss", "accountant", "admin")
def cash_reconciliations():
    try:
        from core.models import CashReconciliation, CashAccount

        accounts = CashAccount.query.order_by(CashAccount.name).all()
        rows = (
            CashReconciliation.query
            .filter(CashReconciliation.is_deleted.is_(False))
            .order_by(CashReconciliation.recon_date.desc(), CashReconciliation.created_at.desc())
            .limit(500)
            .all()
        )
        return render_template("cashier/reconciliations.html", accounts=accounts, reconciliations=rows)
    except Exception:
        logger.exception("cash_reconciliations failed")
        raise


def _get_output_model(mineral_type: str):
    m = _canonical_mineral_type(mineral_type)
    if m == "copper":
        from copper.models import CopperOutput
        return CopperOutput
    if m == "cassiterite":
        from cassiterite.models import CassiteriteOutput
        return CassiteriteOutput
    return None


def _get_stock_model(mineral_type: str):
    m = _canonical_mineral_type(mineral_type)
    if m == "copper":
        from copper.models import CopperStock
        return CopperStock
    if m == "cassiterite":
        from cassiterite.models import CassiteriteStock
        return CassiteriteStock
    return None


def _canonical_mineral_type(mineral_type: str | None) -> str:
    """Return a canonical mineral key used by shared ledger/receipt flows."""
    m = (mineral_type or "").strip().lower()
    if m in {"copper", "coltan"}:
        return "copper"
    if m == "cassiterite":
        return "cassiterite"
    return ""


def _mineral_aliases(mineral_type: str | None) -> tuple[str, ...]:
    """Map canonical mineral to accepted DB aliases for backward compatibility."""
    canonical = _canonical_mineral_type(mineral_type)
    if canonical == "copper":
        return ("copper", "coltan")
    if canonical == "cassiterite":
        return ("cassiterite",)
    return tuple()


def _batch_outstanding_rwf(mineral_type: str, batch_id: str) -> float:
    """Calculate outstanding amount for a batch using SINGLE SOURCE OF TRUTH.
    
    ╔════════════════════════════════════════════════════════════════════╗
    ║ SOURCE: BulkOutputPlan.total_expected_amount - sum(receipts)      ║
    ║         - sum(allocations) - sum(deductions)                      ║
    ║ NO Output rows, NO debt_remaining tracking                         ║
    ╚════════════════════════════════════════════════════════════════════╝
    
    Formula:
        outstanding = total_expected_amount - sum(receipts.amount_rwf)
                      - sum(allocations.applied_amount_rwf)
                      - sum(deductions.amount_rwf)
    
    Args:
        mineral_type: 'copper'|'coltan'|'cassiterite'
        batch_id: Batch identifier
    
    Returns:
        float: Outstanding amount (>= 0)
    
    Used by:
        - _batch_debt_options() - Populate dropdown options
        - update_debts() - Show remaining for selected batch
        - Payment form validation - Check if payment fits
    
    Data Queries:
        1. BulkOutputPlan WHERE batch_id + mineral_type
        2. SUM(CustomerReceipt) WHERE batch_id + mineral_type
        3. SUM(CustomerUnearnedAllocation) WHERE batch_id
        4. SUM(BatchDeduction) WHERE batch_id
    """
    aliases = _mineral_aliases(mineral_type)
    if not aliases:
        return 0.0

    # Get all plans for this batch + mineral
    plans = BulkOutputPlan.query.filter(
        BulkOutputPlan.batch_id == batch_id,
        BulkOutputPlan.mineral_type.in_(aliases),
        BulkOutputPlan.total_expected_amount.isnot(None),
        BulkOutputPlan.total_expected_amount > 0,
    ).all()

    if not plans:
        return 0.0

    # Total agreed amount (in RWF)
    total_expected = float(sum(float(p.total_expected_amount or 0) for p in plans))
    
    # Total paid so far - sum of all CustomerReceipt.amount_rwf (already normalized)
    total_paid = (
        db.session.query(func.coalesce(func.sum(CustomerReceipt.amount_rwf), 0))
        .filter(
            CustomerReceipt.batch_id == batch_id,
            CustomerReceipt.mineral_type.in_(aliases),
        )
        .scalar()
        or 0.0
    )
    total_paid = float(total_paid or 0)

    # Total allocated from unearned receipts (already in RWF)
    total_allocated = (
        db.session.query(func.coalesce(func.sum(CustomerUnearnedAllocation.applied_amount_rwf), 0))
        .filter(
            CustomerUnearnedAllocation.batch_id == batch_id,
            or_(
                CustomerUnearnedAllocation.stock_mineral_type.in_(aliases),
                CustomerUnearnedAllocation.stock_mineral_type.is_(None),
            ),
        )
        .scalar()
        or 0.0
    )
    total_allocated = float(total_allocated or 0)

    # Total deducted (expenses, RMA, transport, etc. - all in RWF)
    # Note: BatchDeduction.batch_id is a FK to BulkOutputPlan.id (integer),
    # not the string batch_id. Get the plan IDs first.
    plan_ids = [p.id for p in plans]
    if plan_ids:
        total_deducted = (
            db.session.query(func.coalesce(func.sum(BatchDeduction.amount_rwf), 0))
            .filter(
                BatchDeduction.batch_id.in_(plan_ids),
            )
            .scalar()
            or 0.0
        )
    else:
        total_deducted = 0.0
    total_deducted = float(total_deducted or 0)

    return float(max(total_expected - total_paid - total_allocated - total_deducted, 0.0))


def _apply_receipt_to_batch(mineral_type: str, batch_id: str, amount_rwf: float, stage: str) -> float:
    """Validate and approve customer payment for batch using SINGLE SOURCE OF TRUTH.
    
    ╔════════════════════════════════════════════════════════════════════╗
    ║ PAYMENT VALIDATION: Check if payment fits in agreed amount         ║
    ║ NO OUTPUT ROW MODIFICATION - they track production only            ║
    ║ IMMUTABLE AUDIT TRAIL: CustomerReceipt created by caller           ║
    ╚════════════════════════════════════════════════════════════════════╝
    
    Purpose:
        Validate that a new payment doesn't exceed the agreed amount.
        Returns the validated amount so caller can create CustomerReceipt.
    
    Args:
        mineral_type: 'copper'|'coltan'|'cassiterite'
        batch_id: Batch identifier
        amount_rwf: Payment amount in RWF
        stage: 'ADVANCE'|'INSTALLMENT'|'FINAL_SETTLEMENT' (unused in validation)
    
    Returns:
        float: amount_rwf if valid (will be used to create CustomerReceipt)
               0.0 if invalid (exceeds plan or no plan found)
    
    Validation Logic:
        1. Get BulkOutputPlan(s) for batch + mineral
        2. Calculate: total_expected = SUM(all plans)
        3. Calculate: total_paid = SUM(CustomerReceipt for same batch)
        4. Check: (total_paid + amount_rwf) <= total_expected + tolerance(0.01)
        5. Return amount_rwf if OK, 0.0 if exceeds
    
    Used by:
        - update_debts() route [line 1846]
        - Called after amount validated, before CustomerReceipt created
    
    Data Queries:
        1. BulkOutputPlan WHERE batch_id + mineral_type
        2. SUM(CustomerReceipt) WHERE batch_id + mineral_type
    
    Note:
        - Does NOT modify any CopperOutput/CassiteriteOutput rows
        - Does NOT create CustomerReceipt (caller does)
        - Output rows stay as-is for production tracking
    """
    aliases = _mineral_aliases(mineral_type)
    if not aliases:
        return 0.0

    amount = float(amount_rwf or 0)
    if amount <= 0:
        return 0.0

    # Get all plans for this batch (may be multiple if split payments)
    plans = BulkOutputPlan.query.filter(
        BulkOutputPlan.batch_id == batch_id,
        BulkOutputPlan.mineral_type.in_(aliases),
        BulkOutputPlan.total_expected_amount.isnot(None),
        BulkOutputPlan.total_expected_amount > 0,
    ).all()

    if not plans:
        logger.warning(f"_apply_receipt_to_batch: no plans found for batch={batch_id} mineral={mineral_type}")
        return 0.0

    # Calculate total plan amount and what's already paid (all in RWF)
    total_plan_amount = float(sum(float(p.total_expected_amount or 0) for p in plans))
    total_paid = (
        db.session.query(func.coalesce(func.sum(CustomerReceipt.amount_rwf), 0))
        .filter(
            CustomerReceipt.batch_id == batch_id,
            CustomerReceipt.mineral_type.in_(aliases),
        )
        .scalar()
        or 0.0
    )
    total_paid = float(total_paid or 0)

    total_allocated = (
        db.session.query(func.coalesce(func.sum(CustomerUnearnedAllocation.applied_amount_rwf), 0))
        .filter(
            CustomerUnearnedAllocation.batch_id == batch_id,
            or_(
                CustomerUnearnedAllocation.stock_mineral_type.in_(aliases),
                CustomerUnearnedAllocation.stock_mineral_type.is_(None),
            ),
        )
        .scalar()
        or 0.0
    )
    total_allocated = float(total_allocated or 0)

    # Check if payment would exceed total plan (tolerance: 0.01 RWF rounding)
    if total_paid + total_allocated + amount > total_plan_amount + 0.01:
        logger.warning(
            f"_apply_receipt_to_batch: payment {amount} would exceed plan {total_plan_amount} "
            f"(already paid {total_paid}, allocated {total_allocated})"
        )
        return 0.0

    # Payment is valid - return the full amount (caller will create CustomerReceipt)
    return amount


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
    # Force fresh data reads to avoid stale query results
    db.session.expire_all()


    # Sales must follow the same source of truth as customer debt:
    # BulkOutputPlan.total_expected_amount (confirmed/executed agreements).
    # Output rows can exist with monetary fields missing/zero.
    copper_total_sales = (
        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
        .filter(
            BulkOutputPlan.mineral_type.in_(_mineral_aliases('copper')),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        .scalar()
        or 0.0
    )
    copper_total_sales = float(copper_total_sales or 0.0)
    copper_cost_basis = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(
        CopperStock.is_deleted.is_(False),
    ).scalar() or 0
    copper_cost_basis = float(copper_cost_basis or 0.0)

    try:
        copper_inventory_value = db.session.query(
            func.coalesce(
                func.sum(CopperStock.net_balance * CopperStock.local_balance / func.nullif(CopperStock.input_kg, 0)),
                0,
            )
        ).filter(
            CopperStock.is_deleted.is_(False),
            CopperStock.local_balance > 0,
            CopperStock.input_kg > 0,
        ).scalar() or 0
    except Exception:
        copper_inventory_value = db.session.query(
            func.coalesce(func.sum(CopperStock.local_balance), 0)
        ).filter(
            CopperStock.is_deleted.is_(False),
            CopperStock.local_balance > 0,
        ).scalar() or 0
        copper_inventory_value = float(copper_inventory_value or 0.0)

    copper_supplier_payments = db.session.query(
        func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0)
    ).filter(SupplierPayment.is_deleted.is_(False)).scalar() or 0
    copper_supplier_payments = float(copper_supplier_payments or 0.0)

    try:
        copper_cost_of_stock_sold = db.session.query(
            func.coalesce(
                func.sum(CopperOutput.output_kg * (CopperStock.net_balance / func.nullif(CopperStock.input_kg, 0))),
                0.0,
            )
        ).join(CopperStock, CopperOutput.stock_id == CopperStock.id).scalar() or 0
    except Exception:
        logger.exception("boss_dashboard: failed to compute copper COGS from outputs; falling back")
        copper_cost_of_stock_sold = (copper_cost_basis or 0) - (copper_inventory_value or 0)
    copper_cost_of_stock_sold = float(copper_cost_of_stock_sold or 0.0)
    copper_gross_profit = float((copper_total_sales or 0.0) - (copper_cost_of_stock_sold or 0.0))

    copper_supplier_debt = float((copper_cost_basis or 0.0) - (copper_supplier_payments or 0.0))
    # Customer debt single source of truth: BulkOutputPlan (agreements) - CustomerReceipt (payments)
    copper_aliases = _mineral_aliases('copper')
    copper_expected = (
        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
        .filter(
            BulkOutputPlan.mineral_type.in_(copper_aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        .scalar()
        or 0.0
    )
    copper_expected = float(copper_expected or 0.0)
    copper_paid = (
        db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
        .filter(CustomerReceipt.mineral_type.in_(copper_aliases))
        .scalar()
        or 0.0
    )
    copper_paid = float(copper_paid or 0.0)
    copper_customer_debt = float(copper_expected or 0.0) - float(copper_paid or 0.0)
    copper_worker_payments = db.session.query(func.coalesce(func.sum(WorkerPayment.amount), 0)).scalar() or 0
    from cassiterite.models.workers_payment import CassiteriteWorkerPayment
    cass_worker_payments = db.session.query(func.coalesce(func.sum(CassiteriteWorkerPayment.amount), 0)).scalar() or 0
    total_internal_worker_payments = copper_worker_payments + cass_worker_payments
    copper_cash_position = copper_total_sales - copper_customer_debt

    cass_total_sales = (
        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
        .filter(
            BulkOutputPlan.mineral_type.in_(_mineral_aliases('cassiterite')),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        .scalar()
        or 0.0
    )
    cass_total_sales = float(cass_total_sales or 0.0)
    cass_cost_basis = db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).filter(
        CassiteriteStock.is_deleted.is_(False),
    ).scalar() or 0
    cass_cost_basis = float(cass_cost_basis or 0.0)

    try:
        cass_inventory_value = db.session.query(
            func.coalesce(
                func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / func.nullif(CassiteriteStock.input_kg, 0)),
                0,
            )
        ).filter(
            CassiteriteStock.is_deleted.is_(False),
            CassiteriteStock.local_balance > 0,
            CassiteriteStock.input_kg > 0,
        ).scalar() or 0
    except Exception:
        cass_inventory_value = db.session.query(
            func.coalesce(func.sum(CassiteriteStock.local_balance), 0)
        ).filter(
            CassiteriteStock.is_deleted.is_(False),
            CassiteriteStock.local_balance > 0,
        ).scalar() or 0
    cass_inventory_value = float(cass_inventory_value or 0.0)

    cass_supplier_payments = db.session.query(
        func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0)
    ).filter(CassiteriteSupplierPayment.is_deleted.is_(False)).scalar() or 0
    cass_supplier_payments = float(cass_supplier_payments or 0.0)

    try:
        cass_cogs = db.session.query(
            func.coalesce(
                func.sum(CassiteriteOutput.output_kg * (CassiteriteStock.balance_to_pay / func.nullif(CassiteriteStock.input_kg, 0))),
                0.0,
            )
        ).join(CassiteriteStock, CassiteriteOutput.stock_id == CassiteriteStock.id).scalar() or 0
    except Exception:
        logger.exception("boss_dashboard: failed to compute cassiterite COGS from outputs; falling back")
        cass_cogs = (cass_cost_basis or 0) - (cass_inventory_value or 0)
    cass_cogs = float(cass_cogs or 0.0)
    cass_cost_of_stock_sold = cass_cogs
    cass_gross_profit = float((cass_total_sales or 0.0) - (cass_cogs or 0.0))

    cass_supplier_debt = float((cass_cost_basis or 0.0) - (cass_supplier_payments or 0.0))
    cass_aliases = _mineral_aliases('cassiterite')
    cass_expected = (
        db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
        .filter(
            BulkOutputPlan.mineral_type.in_(cass_aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        .scalar()
        or 0.0
    )
    cass_paid = (
        db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
        .filter(CustomerReceipt.mineral_type.in_(cass_aliases))
        .scalar()
        or 0.0
    )
    cass_customer_debt = float(cass_expected or 0.0) - float(cass_paid or 0.0)
    cass_cash_position = cass_total_sales - cass_customer_debt

    total_gross_profit = float((copper_gross_profit or 0.0) + (cass_gross_profit or 0.0))
    total_inventory_value = float((copper_inventory_value or 0.0) + (cass_inventory_value or 0.0))
    total_cost_of_stock_sold = float((copper_cost_of_stock_sold or 0.0) + (cass_cost_of_stock_sold or 0.0))
    total_supplier_debt = float((copper_supplier_debt or 0.0) + (cass_supplier_debt or 0.0))
    total_customer_debt = float((copper_customer_debt or 0.0) + (cass_customer_debt or 0.0))
    total_sales = float((copper_total_sales or 0.0) + (cass_total_sales or 0.0))
    total_internal_expenses = float(total_internal_worker_payments or 0.0)
    total_cash_at_hand = float(total_sales - total_customer_debt - total_internal_expenses)
    total_net_profit = float(total_gross_profit - total_internal_expenses)

    pending_reviews = PaymentReview.query.filter_by(
        status=PaymentReviewStatus.PENDING_REVIEW.value
    ).order_by(PaymentReview.created_at.desc()).limit(100).all()

    cash_account_requests = []
    try:
        cash_account_requests = (
            Notification.query
            .filter(
                Notification.user_id == getattr(current_user, 'id', None),
                Notification.type == 'CASH_ACCOUNT_REQUESTED',
                Notification.read_at.is_(None),
            )
            .order_by(Notification.created_at.desc())
            .limit(50)
            .all()
        )
    except Exception:
        cash_account_requests = []
    for r in (pending_reviews or []):
        p = _safe_payload(getattr(r, 'request_payload', None))
        if not p:
            p = _safe_payload(getattr(r, 'boss_comment', None))
        r.request_payload_reference = (p.get('reference') or '').strip() if isinstance(p, dict) else ''
        r.request_payload_note = (p.get('note') or '').strip() if isinstance(p, dict) else ''
        r.related_url = None
        r.related_label = None
        if isinstance(p, dict):
            action = (p.get('action') or '').strip().lower()
            if action in {'collect_receipt', 'cash_collect_receipt'} and p.get('receipt_id'):
                r.related_url = url_for('core.customer_receipt_detail', receipt_id=int(p.get('receipt_id')))
                r.related_label = f"receipt:{int(p.get('receipt_id'))}"
            elif action in {'collect_unearned_receipt', 'cash_collect_unearned_receipt'} and p.get('unearned_id'):
                r.related_url = url_for('core.customer_unearned_receipt_detail', unearned_id=int(p.get('unearned_id')))
                r.related_label = f"unearned:{int(p.get('unearned_id'))}"
    for r in pending_reviews:
        r.display_comment = _review_details(r)
        amount_breakdown = _review_amount_breakdown(r)
        r.display_amount = amount_breakdown["primary"]
        r.display_amount_note = amount_breakdown["note"]
        r.display_amount_details = amount_breakdown["details"]

    from datetime import datetime as _dt
    raw_reviews = (
        PaymentReview.query
        .filter(PaymentReview.status != PaymentReviewStatus.PENDING_REVIEW.value)
        .order_by(PaymentReview.created_at.desc())
        .limit(200)
        .all()
    )
    by_payment: dict = {}
    for r in raw_reviews:
        review_type = (r.type or "").strip().lower()
        if review_type in {"stock_delete", "stock_edit"}:
            key = f"rev-{r.id}"
        else:
            key = r.payment_id if r.payment_id is not None else f"rev-{r.id}"
        best = by_payment.get(key)
        r_time = r.reviewed_at or r.created_at
        best_time = (best.reviewed_at or best.created_at) if best else None
        if not best or (r_time and (not best_time or r_time > best_time)):
            by_payment[key] = r
    recent_reviews = sorted(
        by_payment.values(),
        key=lambda x: (x.reviewed_at or x.created_at) or _dt.min,
        reverse=True,
    )[:20]
    for r in recent_reviews:
        p = _safe_payload(getattr(r, 'request_payload', None))
        if not p:
            p = _safe_payload(getattr(r, 'boss_comment', None))
        r.request_payload_reference = (p.get('reference') or '').strip() if isinstance(p, dict) else ''
        r.request_payload_note = (p.get('note') or '').strip() if isinstance(p, dict) else ''
        r.related_url = None
        r.related_label = None
        if isinstance(p, dict):
            action = (p.get('action') or '').strip().lower()
            if action in {'collect_receipt', 'cash_collect_receipt'} and p.get('receipt_id'):
                r.related_url = url_for('core.customer_receipt_detail', receipt_id=int(p.get('receipt_id')))
                r.related_label = f"receipt:{int(p.get('receipt_id'))}"
            elif action in {'collect_unearned_receipt', 'cash_collect_unearned_receipt'} and p.get('unearned_id'):
                r.related_url = url_for('core.customer_unearned_receipt_detail', unearned_id=int(p.get('unearned_id')))
                r.related_label = f"unearned:{int(p.get('unearned_id'))}"
        r.display_comment = _review_details(r)
        amount_breakdown = _review_amount_breakdown(r)
        r.display_amount = amount_breakdown["primary"]
        r.display_amount_note = amount_breakdown["note"]
        r.display_amount_details = amount_breakdown["details"]

    recent_plans = BulkOutputPlan.query.order_by(BulkOutputPlan.created_at.desc()).limit(20).all()
    show_payment_reviews = getattr(current_user, "role", None) in {"boss", "admin"}

    return render_template(
        "boss/dashboard.html",
        copper_total_sales=copper_total_sales,
        copper_cost_basis=copper_cost_basis,
        copper_inventory_value=copper_inventory_value,
        copper_cost_of_stock_sold=copper_cost_of_stock_sold,
        copper_gross_profit=copper_gross_profit,
        copper_supplier_debt=copper_supplier_debt,
        copper_customer_debt=copper_customer_debt,
        copper_cash_position=copper_cash_position,
        cass_total_sales=cass_total_sales,
        cass_cost_basis=cass_cost_basis,
        cass_inventory_value=cass_inventory_value,
        cass_cost_of_stock_sold=cass_cost_of_stock_sold,
        cass_gross_profit=cass_gross_profit,
        cass_supplier_debt=cass_supplier_debt,
        cass_customer_debt=cass_customer_debt,
        cass_cash_position=cass_cash_position,
        total_gross_profit=total_gross_profit,
        total_inventory_value=total_inventory_value,
        total_cost_of_stock_sold=total_cost_of_stock_sold,
        total_supplier_debt=total_supplier_debt,
        total_customer_debt=total_customer_debt,
        total_internal_worker_payments=total_internal_worker_payments,
        total_internal_expenses=total_internal_expenses,
        total_cash_at_hand=total_cash_at_hand,
        total_net_profit=total_net_profit,
        pending_reviews=pending_reviews,
        cash_account_requests=cash_account_requests,
        show_payment_reviews=show_payment_reviews,
        recent_reviews=recent_reviews,
        recent_plans=recent_plans,
    )


@core_bp.route('/api/boss/dashboard/summary', methods=['GET'])
@role_required('boss', 'admin')
def boss_dashboard_summary():
    pending_count = (
        PaymentReview.query
        .filter(PaymentReview.status == PaymentReviewStatus.PENDING_REVIEW.value)
        .count()
    )

    cash_account_req_count = 0
    try:
        cash_account_req_count = (
            Notification.query
            .filter(
                Notification.user_id == getattr(current_user, 'id', None),
                Notification.type == 'CASH_ACCOUNT_REQUESTED',
                Notification.read_at.is_(None),
            )
            .count()
        )
    except Exception:
        cash_account_req_count = 0

    return safe_jsonify({'pending_reviews': int(pending_count or 0), 'cash_account_requests': int(cash_account_req_count or 0)})


@core_bp.route("/boss/dashboard/data")
@role_required("boss","admin","accountant")
def boss_dashboard_data():
    """Return dashboard data as JSON for AJAX updates.

    Supports optional query params: mineral, from, to
    """
    mineral = request.args.get('mineral') or ''
    date_from = request.args.get('from') or None
    date_to = request.args.get('to') or None
    # Force fresh data reads to avoid stale query results on filter updates
    db.session.expire_all()


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
        # Sales must be in sync with customer ledger truth (plans).
        aliases = _mineral_aliases('copper')
        plans_q = BulkOutputPlan.query.filter(
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        if d_from:
            plans_q = plans_q.filter(BulkOutputPlan.created_at >= datetime.combine(d_from, time.min))
        if d_to:
            plans_q = plans_q.filter(BulkOutputPlan.created_at <= datetime.combine(d_to, time.max))
        copper_total_sales = float(plans_q.with_entities(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0)).scalar() or 0.0)

        # Stock-side: restrict to lots in the date window (original supplier
        # cost basis is by stock.date).
        stock_q = CopperStock.query.filter(CopperStock.is_deleted.is_(False))
        if d_from:
            stock_q = stock_q.filter(CopperStock.date >= d_from)
        if d_to:
            stock_q = stock_q.filter(CopperStock.date <= d_to)

        # Original cost basis for this window
        copper_cost_basis = float(stock_q.with_entities(func.coalesce(func.sum(CopperStock.net_balance), 0)).scalar() or 0.0)

        # Inventory Value (current cost of remaining Coltan stock in this window)
        inv_q = stock_q.filter(CopperStock.local_balance > 0, CopperStock.input_kg > 0)
        copper_inventory_value = float(inv_q.with_entities(
            func.coalesce(
                func.sum(CopperStock.net_balance * CopperStock.local_balance / CopperStock.input_kg),
                0,
            )
        ).scalar() or 0.0)

        # Supplier payments filtered by the same stock window
        pay_q = db.session.query(
            func.coalesce(func.sum(func.coalesce(SupplierPayment.amount_rwf, SupplierPayment.amount)), 0)
        ).filter(
            SupplierPayment.is_deleted.is_(False),
        )
        # Best effort: date filter supplier payments by their own paid_at timestamp.
        if d_from:
            pay_q = pay_q.filter(SupplierPayment.paid_at >= datetime.combine(d_from, time.min))
        if d_to:
            pay_q = pay_q.filter(SupplierPayment.paid_at <= datetime.combine(d_to, time.max))
        copper_supplier_payments = float(pay_q.scalar() or 0.0)

        # COGS for this window and gross profit = Sales - COGS
        copper_cogs = float((copper_cost_basis or 0.0) - (copper_inventory_value or 0.0))
        copper_gross_profit = float((copper_total_sales or 0.0) - (copper_cogs or 0.0))

        # Supplier debt = original supplier cost - payments
        copper_supplier_debt = float(copper_cost_basis - copper_supplier_payments)
        plans_q = BulkOutputPlan.query.filter(
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        receipts_q = CustomerReceipt.query.filter(CustomerReceipt.mineral_type.in_(aliases))
        if d_from:
            plans_q = plans_q.filter(BulkOutputPlan.created_at >= datetime.combine(d_from, time.min))
            receipts_q = receipts_q.filter(CustomerReceipt.received_at >= datetime.combine(d_from, time.min))
        if d_to:
            plans_q = plans_q.filter(BulkOutputPlan.created_at <= datetime.combine(d_to, time.max))
            receipts_q = receipts_q.filter(CustomerReceipt.received_at <= datetime.combine(d_to, time.max))
        expected = plans_q.with_entities(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0)).scalar() or 0.0
        paid = receipts_q.with_entities(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0)).scalar() or 0.0
        copper_customer_debt = float(expected or 0.0) - float(paid or 0.0)
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
        aliases = _mineral_aliases('cassiterite')
        plans_q = BulkOutputPlan.query.filter(
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        if d_from:
            plans_q = plans_q.filter(BulkOutputPlan.created_at >= datetime.combine(d_from, time.min))
        if d_to:
            plans_q = plans_q.filter(BulkOutputPlan.created_at <= datetime.combine(d_to, time.max))
        cass_total_sales = float(plans_q.with_entities(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0)).scalar() or 0.0)

        # Stock-side cost basis by lot.date
        stock_q = CassiteriteStock.query.filter(CassiteriteStock.is_deleted.is_(False))
        if d_from:
            stock_q = stock_q.filter(CassiteriteStock.date >= d_from)
        if d_to:
            stock_q = stock_q.filter(CassiteriteStock.date <= d_to)

        cass_cost_basis = float(stock_q.with_entities(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0)).scalar() or 0.0)

        inv_q = stock_q.filter(CassiteriteStock.local_balance > 0, CassiteriteStock.input_kg > 0)
        cass_inventory_value = float(inv_q.with_entities(
            func.coalesce(
                func.sum(CassiteriteStock.balance_to_pay * CassiteriteStock.local_balance / CassiteriteStock.input_kg),
                0,
            )
        ).scalar() or 0.0)

        pay_q = db.session.query(
            func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0)
        ).filter(
            CassiteriteSupplierPayment.is_deleted.is_(False),
        )
        if d_from:
            pay_q = pay_q.filter(CassiteriteSupplierPayment.paid_at >= datetime.combine(d_from, time.min))
        if d_to:
            pay_q = pay_q.filter(CassiteriteSupplierPayment.paid_at <= datetime.combine(d_to, time.max))
        cass_supplier_payments = float(pay_q.scalar() or 0.0)

        # COGS for this window and gross profit = Sales - COGS
        cass_cogs = float((cass_cost_basis or 0.0) - (cass_inventory_value or 0.0))
        cass_gross_profit = float((cass_total_sales or 0.0) - (cass_cogs or 0.0))
        cass_supplier_debt = float((cass_cost_basis or 0.0) - (cass_supplier_payments or 0.0))
        plans_q = BulkOutputPlan.query.filter(
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        receipts_q = CustomerReceipt.query.filter(CustomerReceipt.mineral_type.in_(aliases))
        if d_from:
            plans_q = plans_q.filter(BulkOutputPlan.created_at >= datetime.combine(d_from, time.min))
            receipts_q = receipts_q.filter(CustomerReceipt.received_at >= datetime.combine(d_from, time.min))
        if d_to:
            plans_q = plans_q.filter(BulkOutputPlan.created_at <= datetime.combine(d_to, time.max))
            receipts_q = receipts_q.filter(CustomerReceipt.received_at <= datetime.combine(d_to, time.max))
        expected = plans_q.with_entities(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0)).scalar() or 0.0
        paid = receipts_q.with_entities(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0)).scalar() or 0.0
        cass_customer_debt = float(expected or 0.0) - float(paid or 0.0)
        wp_q = CassiteriteWorkerPayment.query
        try:
            if d_from:
                wp_q = wp_q.filter(CassiteriteWorkerPayment.paid_at >= datetime.combine(d_from, time.min))
            if d_to:
                wp_q = wp_q.filter(CassiteriteWorkerPayment.paid_at <= datetime.combine(d_to, time.max))
        except Exception:
            wp_q = CassiteriteWorkerPayment.query
        cass_worker_payments = float(wp_q.with_entities(func.coalesce(func.sum(CassiteriteWorkerPayment.amount), 0)).scalar() or 0.0)
        cass_cash_position = float(cass_total_sales - cass_customer_debt)
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
    total_internal_expenses = total_internal_worker_payments
    total_sales = (copper['total_sales'] if copper else 0) + (cass['total_sales'] if cass else 0)
    total_cash_at_hand = total_sales - total_customer_debt - total_internal_expenses
    total_net_profit = total_gross_profit - total_internal_expenses

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
        total_kg = 0.0
        if p.plan_json:
            for row in p.plan_json:
                # plan_json rows may include metadata dicts or missing qty.
                qty_val = 0.0
                if isinstance(row, dict):
                    try:
                        qty_val = float(row.get('planned_output_kg') or 0)
                    except Exception:
                        qty_val = 0.0
                else:
                    try:
                        qty_val = float(getattr(row, 'planned_output_kg', 0) or 0)
                    except Exception:
                        qty_val = 0.0
                if qty_val > 0:
                    total_kg += qty_val

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
        'total_internal_expenses': total_internal_expenses,
        'total_cash_at_hand': total_cash_at_hand,
        'total_net_profit': total_net_profit,
    }

    return safe_jsonify({
        'kpis': kpis,
        'copper': copper,
        'cassiterite': cass,
        'recent_plans': recent_plans,
    })


@core_bp.route('/boss/adjustments')
@role_required('boss', 'admin', 'accountant')
def boss_stock_adjustments():
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    per_page = int(request.args.get('per_page', 50))
    if per_page < 1:
        per_page = 50
    if per_page > 200:
        per_page = 200

    mineral = (request.args.get('mineral') or '').strip().lower() or None
    action = (request.args.get('action') or '').strip().upper() or None

    q = StockChangeLog.query
    if mineral:
        q = q.filter(StockChangeLog.mineral_type == mineral)
    if action:
        q = q.filter(StockChangeLog.action == action)

    total = q.count()
    rows = (
        q.order_by(StockChangeLog.created_at.desc(), StockChangeLog.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )

    return render_template(
        'boss/stock_adjustments.html',
        rows=rows,
        page=page,
        per_page=per_page,
        total=total,
        mineral=mineral,
        action=action,
    )


@core_bp.route('/boss/adjustments/<int:log_id>')
@role_required('boss', 'admin', 'accountant')
def boss_stock_adjustment_detail(log_id: int):
    row = StockChangeLog.query.get_or_404(log_id)
    return render_template('boss/stock_adjustment_detail.html', row=row)


@core_bp.route('/boss/adjustments/<int:log_id>/edit', methods=['GET', 'POST'])
@role_required('boss', 'admin')
def boss_stock_adjustment_edit(log_id: int):
    """Edit the reason field of a stock adjustment log with full audit trail."""
    row = StockChangeLog.query.get_or_404(log_id)
    
    if request.method == 'POST':
        new_reason = request.form.get('reason', '').strip()
        edit_reason = request.form.get('edit_reason', '').strip()
        
        if not new_reason:
            flash('Reason cannot be empty.', 'danger')
            return render_template('boss/stock_adjustment_edit.html', row=row)
        
        if not edit_reason:
            flash('You must provide a reason for editing this adjustment.', 'danger')
            return render_template('boss/stock_adjustment_edit.html', row=row)
        
        try:
            # Store original reason before editing
            if not row.original_reason:
                row.original_reason = row.reason
            
            # Update the reason
            row.reason = new_reason
            row.reason_edited_by_id = getattr(current_user, 'id', None)
            row.reason_edited_at = datetime.utcnow()
            row.reason_edit_reason = edit_reason
            
            db.session.commit()
            
            # Notify all bosses about the edit
            boss_rows = db.session.query(User.id).filter_by(role="boss", is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=boss_id,
                    type_="adjustment_edit",
                    message=f"Adjustment log #{log_id} reason was edited by {getattr(current_user, 'username', 'unknown')}.",
                    related_type="stock_change_log",
                    related_id=log_id
                )
            
            flash('Adjustment reason updated successfully.', 'success')
            return redirect(url_for('core.boss_stock_adjustment_detail', log_id=log_id))
        except Exception as e:
            db.session.rollback()
            logger.exception("boss_stock_adjustment_edit failed")
            flash(f'Error updating adjustment: {e}', 'danger')
    
    return render_template('boss/stock_adjustment_edit.html', row=row)


@core_bp.route("/boss/payment_review/<int:review_id>/approve", methods=["POST"])
@role_required("boss")
def boss_approve_payment(review_id: int):
    """Boss approves a pending payment review.

    When this happens we also notify all active accountants so they can
    see that the boss has approved the payment.
    """
    review = PaymentReview.query.get_or_404(review_id)
    if review.status != PaymentReviewStatus.PENDING_REVIEW.value:
        flash("Only pending payment reviews can be approved.", "warning")
        return redirect(url_for("core.boss_dashboard"))

    payload = _safe_payload(getattr(review, "request_payload", None))
    if not payload:
        payload = _safe_payload(review.boss_comment)
    review_type = (review.type or "").strip().lower()
    mineral = (review.mineral_type or "").strip().lower()

    try:
        amount = float(review.amount or 0)
        currency = (payload.get("currency") or review.currency or "RWF").upper()
        amount_input = float(payload.get("amount_input") or review.amount or 0)
        amount_rwf = float(payload.get("amount_rwf") or amount)

        # Boss approval is authorization only. Cashier disbursement will execute.
        review.status = PaymentReviewStatus.APPROVED.value
        review.reviewed_by_id = getattr(current_user, "id", None)
        from datetime import datetime as _dt
        review.reviewed_at = _dt.utcnow()

        # Special-case: ledger-only transporter fee charge should be applied on boss approval
        try:
            action = (payload.get('action') or '').strip().lower()
            if action == 'charge_transporter_fee' or review_type == 'transporter_fee_charge':
                from core.models import TransporterLedger

                transporter_name = (payload.get('transporter_name') or review.customer or '').strip()
                if not transporter_name:
                    raise ValueError('Missing transporter name for charge.')

                entry_kind = (payload.get('entry_kind') or 'TRANSPORTER_FEE_CHARGE').strip().upper()
                ledger_amount = float(amount_rwf or 0.0)

                # Charge reduces what we owe the transporter: store as negative RWF amount
                ledger_row = TransporterLedger(
                    transporter_name=transporter_name,
                    supplier_name=None,
                    entry_type=entry_kind,
                    amount_input=float(amount_input or 0.0),
                    currency=currency,
                    exchange_rate=float(payload.get('exchange_rate') or 1.0),
                    amount_rwf=float(-abs(ledger_amount)),
                    is_paid=False,
                    created_by_id=getattr(current_user, 'id', None),
                    note=payload.get('note') or f'Transporter fee charged by boss - {transporter_name}',
                    payment_review_id=int(review.id),
                )
                db.session.add(ledger_row)
                db.session.flush()

                review.disbursement_status = 'DISBURSED'
                review.disbursed_by_id = getattr(current_user, 'id', None)
                review.disbursed_at = _dt.utcnow()
                review.boss_comment = (review.boss_comment or '') + f" | transporter_fee_charge_ledger_id={int(ledger_row.id)}"
                db.session.add(review)
                # Notify accountants/cashiers that a ledger-only charge was applied
                boss_rows2 = db.session.query(User.id).filter_by(role='accountant', is_active=True).all()
                for (acct_id,) in boss_rows2:
                    create_notification(
                        user_id=int(acct_id),
                        type_='TRANSPORTER_FEE_CHARGED',
                        message=(f"Boss approved transporter fee charge for {transporter_name}: {amount_input:,.2f} {currency}."),
                        related_type='payment_review',
                        related_id=int(review.id),
                    )
        except Exception:
            # If ledger-only write fails, log but continue so boss approval still persists.
            logger.exception('Failed to apply transporter fee charge ledger on boss approval')

        if review_type == 'loan_disbursement':
            try:
                loan_id = int(payload.get('loan_id') or 0)
            except Exception:
                loan_id = 0
            if loan_id:
                loan = Loan.query.get(loan_id)
                if loan:
                    loan.status = 'APPROVED'
                    loan.boss_approved_by_id = getattr(current_user, 'id', None)
                    loan.boss_approved_at = _dt.utcnow()
                    db.session.add(loan)

        if review_type == 'batch_agreement':
            try:
                plan_id = int(payload.get('plan_id') or 0)
            except Exception:
                plan_id = 0
            if not plan_id:
                raise ValueError('Missing plan_id for agreement approval.')
            plan = BulkOutputPlan.query.get(plan_id)
            if not plan:
                raise ValueError('BulkOutputPlan not found.')

            submitted_customer = (payload.get('customer') or '').strip()
            total_expected_amount = float(payload.get('total_expected_amount') or 0.0)
            if not submitted_customer:
                raise ValueError('Invalid agreement details.')

            existing_customer = (plan.customer or '').strip()
            if existing_customer and existing_customer.lower() != submitted_customer.lower():
                raise ValueError(
                    f"This batch already has a customer '{existing_customer}'. You cannot overwrite it. Create a new batch instead."
                )

            plan.customer = submitted_customer
            if total_expected_amount > 0:
                plan.total_expected_amount = float(total_expected_amount)

            # Persist currency and exchange_rate if provided in payload
            try:
                pay_currency = (payload.get('currency') or review.currency or 'RWF').upper()
                plan.currency = pay_currency
            except Exception:
                plan.currency = 'RWF'

            try:
                plan.exchange_rate = float(payload.get('exchange_rate') or 1.0)
            except Exception:
                plan.exchange_rate = 1.0

            # Create BatchDeduction rows only when an agreed total exists.
            if total_expected_amount > 0:
                try:
                    ded_list = payload.get('deductions') or []
                    for d in ded_list:
                        d_type = (d.get('type') or '').strip().upper()
                        try:
                            amt = float(d.get('amount') or 0)
                        except Exception:
                            amt = 0.0
                        if not d_type or amt <= 0:
                            continue

                        bd = BatchDeduction(
                            batch_id=int(plan.id),
                            deduction_type=d_type,
                            amount_input=amt,
                            currency=plan.currency,
                            exchange_rate=plan.exchange_rate,
                            amount_rwf=amt * float(plan.exchange_rate or 1.0),
                            created_by_id=getattr(review, 'created_by_id', None),
                        )
                        db.session.add(bd)
                except Exception:
                    logger.exception('boss_approve_payment: failed to create BatchDeduction rows')

            output_model = _get_output_model(plan.mineral_type)
            if output_model:
                existing_output_customer = (
                    db.session.query(output_model.customer)
                    .filter(
                        output_model.batch_id == plan.batch_id,
                        output_model.customer.isnot(None),
                        func.length(func.trim(output_model.customer)) > 0,
                    )
                    .limit(1)
                    .scalar()
                )
                if existing_output_customer and str(existing_output_customer).strip().lower() != submitted_customer.lower():
                    raise ValueError(
                        f"This batch already has outputs recorded for customer '{existing_output_customer}'. You cannot overwrite it."
                    )
                db.session.query(output_model).filter_by(batch_id=plan.batch_id).update({'customer': submitted_customer})
            db.session.add(plan)

            # Auto-allocate any existing unearned receipts only when a real agreed amount exists.
            if total_expected_amount > 0:
                try:
                    paid_q = db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0)).filter(CustomerReceipt.batch_id == plan.batch_id).scalar() or 0.0
                    allocated_q = db.session.query(func.coalesce(func.sum(CustomerUnearnedAllocation.applied_amount_rwf), 0)).filter(CustomerUnearnedAllocation.batch_id == plan.batch_id).scalar() or 0.0
                    remaining_to_allocate = float((plan.total_expected_amount or 0.0) - float(paid_q or 0.0) - float(allocated_q or 0.0))
                    if remaining_to_allocate > 0:
                        unearned_rows = (
                            CustomerUnearnedReceipt.query
                            .filter(CustomerUnearnedReceipt.customer == submitted_customer, CustomerUnearnedReceipt.remaining_rwf > 0)
                            .order_by(CustomerUnearnedReceipt.received_at.asc(), CustomerUnearnedReceipt.id.asc())
                            .all()
                        )
                        for ur in unearned_rows:
                            if remaining_to_allocate <= 0:
                                break
                            avail = float(ur.remaining_rwf or 0.0)
                            if avail <= 0:
                                continue
                            apply_amt = min(avail, remaining_to_allocate)
                            alloc = CustomerUnearnedAllocation(
                                unearned_id=int(ur.id),
                                batch_id=plan.batch_id,
                                stock_mineral_type=(plan.mineral_type or '').strip().lower() or None,
                                applied_amount_rwf=float(apply_amt),
                                created_by_id=getattr(current_user, 'id', None),
                                created_at=datetime.utcnow(),
                            )
                            db.session.add(alloc)
                            ur.remaining_rwf = float((ur.remaining_rwf or 0.0) - apply_amt)
                            if ur.remaining_rwf < 0:
                                ur.remaining_rwf = 0.0
                            remaining_to_allocate -= apply_amt
                            db.session.flush()
                except Exception:
                    logger.exception('boss_approve_payment: failed to allocate unearned receipts to batch')

            # Notify negotiator who requested it
            if getattr(review, 'created_by_id', None):
                pay_currency = (payload.get('currency') or review.currency or 'RWF').upper()
                if total_expected_amount <= 0:
                    status_note = 'draft approved without agreed total yet'
                else:
                    status_note = f'igiciro cyose: {total_expected_amount:,.2f} {pay_currency}'
                create_notification(
                    user_id=int(review.created_by_id),
                    type_='BATCH_AGREEMENT_APPROVED',
                    message=(
                        f"Deal ya batch {plan.batch_id} ({(plan.mineral_type or '').upper()}) yemerewe na Boss. "
                        f"Umukiriya: {submitted_customer}, {status_note}."
                    ),
                    related_type='bulk_plan',
                    related_id=int(plan.id),
                )

        if review_type.startswith('cash_') or review_type == 'cash_transaction' or (payload.get('action') in {'cash_transaction', 'collect_receipt', 'supplier_refund'}):
            message = (
                f"Boss approved cash action for {review.customer} "
                f"({amount_rwf:,.2f} RWF from {amount_input:,.2f} {currency})."
            )
        else:
            message = (
                f"Boss approved {review.mineral_type} payment for "
                f"{review.customer} ({amount_rwf:,.2f} RWF from {amount_input:,.2f} {currency})."
            )

        # If this approval was a request to change/delete a stock, apply it now.
        try:
            action = (payload.get('action') or '').strip().lower()
            if action in {'delete_stock', 'edit_stock'}:
                stock_id = int(payload.get('stock_id') or review.payment_id or 0)
                mineral_key = (payload.get('mineral_type') or review.mineral_type or '').strip().lower()
                stock_model = _get_stock_model(mineral_key)
                if not stock_model:
                    raise ValueError(f"Unknown mineral type for approval execution: {mineral_key}")

                # Load the stock and prepare snapshots
                stock = stock_model.query.get(stock_id)
                if not stock:
                    raise ValueError(f"Stock id {stock_id} not found for auto-apply.")

                # Common before snapshot
                before_snapshot = {
                    'id': int(stock.id),
                    'date': str(getattr(stock, 'date', None)) if getattr(stock, 'date', None) else None,
                    'voucher_no': getattr(stock, 'voucher_no', None),
                    'supplier': getattr(stock, 'supplier', None),
                    'input_kg': float(getattr(stock, 'input_kg', 0.0) or 0.0),
                    'percentage': float(getattr(stock, 'percentage', 0.0) or 0.0),
                    'local_balance': float(getattr(stock, 'local_balance', 0.0) or 0.0),
                    't_unity': float(getattr(stock, 't_unity', 0.0) or 0.0),
                }

                if action == 'delete_stock':
                    # compute contribution and mark deleted
                    try:
                        contrib_q, contrib_wp, contrib_t = stock_model.contribution(stock)
                    except Exception:
                        contrib_q = contrib_wp = contrib_t = 0.0

                    delete_reason = payload.get('delete_reason') or payload.get('reason') or f"Auto-deleted by boss approval (review #{review.id})."
                    stock.is_deleted = True
                    stock.deleted_at = datetime.utcnow()
                    stock.deleted_by_id = getattr(current_user, 'id', None)
                    try:
                        stock.delete_reason = delete_reason
                    except Exception:
                        pass
                    db.session.add(stock)

                    try:
                        log_row = StockChangeLog(
                            mineral_type=mineral_key,
                            stock_id=int(stock.id),
                            action='DELETE',
                            reason=delete_reason,
                            before_json=before_snapshot,
                            after_json={'is_deleted': True},
                            created_by_id=getattr(review, 'created_by_id', None),
                        )
                        db.session.add(log_row)
                        db.session.flush()
                    except Exception:
                        logger.exception('boss_approve_payment: failed to create StockChangeLog for auto-delete')
                        log_row = None

                    try:
                        stock_model.apply_aggregate_delta(-contrib_q, -contrib_wp, -contrib_t, mineral_type=mineral_key)
                    except Exception:
                        logger.exception('boss_approve_payment: failed to apply aggregate delta for auto-delete')

                    # Notify accountants that the boss approved and action was applied
                    accountant_rows = db.session.query(User.id).filter_by(role='accountant', is_active=True).all()
                    for (acc_id,) in accountant_rows:
                        create_notification(
                            user_id=acc_id,
                            type_='STOCK_DELETE_AUTO_APPLIED',
                            message=f"Umuyobozi yemeje kandi yatanze ingano {before_snapshot.get('voucher_no')} (bijyanye #{review.id}).",
                            related_type='stock_change_log' if log_row else 'stock',
                            related_id=(int(getattr(log_row, 'id', 0)) if log_row else int(stock.id)),
                        )

                    # Invalidate copper dashboard cache if present
                    try:
                        if mineral_key in {'copper', 'coltan'}:
                            from copper.routes.stock_routes import _set_dashboard_aggregates as _cset
                            _cset(None, ttl=0)
                    except Exception:
                        pass

                elif action == 'edit_stock':
                    # Apply edit payload fields onto the stock then recompute
                    change_reason = payload.get('change_reason') or payload.get('reason') or f"Auto-edit by boss approval (review #{review.id})."
                    # capture old contribution
                    try:
                        old_q, old_wp, old_t = stock_model.contribution(stock)
                    except Exception:
                        old_q = old_wp = old_t = 0.0

                    # Apply fields (best-effort)
                    try:
                        if payload.get('date'):
                            from datetime import datetime as _dt2
                            try:
                                stock.date = _dt2.fromisoformat(payload.get('date')).date()
                            except Exception:
                                try:
                                    stock.date = _dt2.strptime(payload.get('date'), '%Y-%m-%d').date()
                                except Exception:
                                    pass
                        if payload.get('new_voucher_no'):
                            stock.voucher_no = payload.get('new_voucher_no')
                        if payload.get('voucher_no') and not payload.get('new_voucher_no'):
                            # keep original voucher if new not provided
                            stock.voucher_no = payload.get('voucher_no')
                        if payload.get('supplier') is not None:
                            stock.supplier = payload.get('supplier')
                        # numeric fields
                        for f in ['input_kg', 'percentage', 'nb', 'u_price', 'lme', 'm_lme', 'sec', 'tc', 'exchange', 'transport_tag']:
                            if f in payload and payload.get(f) is not None:
                                try:
                                    setattr(stock, f, float(payload.get(f)))
                                except Exception:
                                    pass

                    except Exception:
                        logger.exception('boss_approve_payment: failed to apply edit payload fields')

                    # Recompute derived values if model exposes update_calculations
                    try:
                        if hasattr(stock, 'update_calculations'):
                            stock.update_calculations()
                    except Exception:
                        logger.exception('boss_approve_payment: failed to run update_calculations on edited stock')

                    # compute new contribution and apply delta
                    try:
                        new_q, new_wp, new_t = stock_model.contribution(stock)
                        delta_q = float((new_q or 0.0) - (old_q or 0.0))
                        delta_wp = float((new_wp or 0.0) - (old_wp or 0.0))
                        delta_t = float((new_t or 0.0) - (old_t or 0.0))
                        stock_model.apply_aggregate_delta(delta_q, delta_wp, delta_t, mineral_type=mineral_key)
                    except Exception:
                        logger.exception('boss_approve_payment: failed to apply aggregate delta for auto-edit')

                    # create change log
                    try:
                        after_snapshot = {
                            'id': int(stock.id),
                            'date': str(getattr(stock, 'date', None)) if getattr(stock, 'date', None) else None,
                            'voucher_no': getattr(stock, 'voucher_no', None),
                            'supplier': getattr(stock, 'supplier', None),
                            'input_kg': float(getattr(stock, 'input_kg', 0.0) or 0.0),
                            'percentage': float(getattr(stock, 'percentage', 0.0) or 0.0),
                            'local_balance': float(getattr(stock, 'local_balance', 0.0) or 0.0),
                            't_unity': float(getattr(stock, 't_unity', 0.0) or 0.0),
                        }
                        log_row = StockChangeLog(
                            mineral_type=mineral_key,
                            stock_id=int(stock.id),
                            action='EDIT',
                            reason=change_reason,
                            before_json=before_snapshot,
                            after_json=after_snapshot,
                            created_by_id=getattr(review, 'created_by_id', None),
                        )
                        db.session.add(log_row)
                        db.session.flush()
                    except Exception:
                        logger.exception('boss_approve_payment: failed to create StockChangeLog for auto-edit')
                        log_row = None

                    # Notify accountants of auto-applied edit
                    accountant_rows = db.session.query(User.id).filter_by(role='accountant', is_active=True).all()
                    for (acc_id,) in accountant_rows:
                        create_notification(
                            user_id=acc_id,
                            type_='STOCK_EDIT_AUTO_APPLIED',
                            message=f"Umuyobozi yemeje kandi yahinduje ingano {before_snapshot.get('voucher_no')} (bijyanye #{review.id}).",
                            related_type='stock_change_log' if log_row else 'stock',
                            related_id=(int(getattr(log_row, 'id', 0)) if log_row else int(stock.id)),
                        )

                    # Invalidate copper cache if needed
                    try:
                        if mineral_key in {'copper', 'coltan'}:
                            from copper.routes.stock_routes import _set_dashboard_aggregates as _cset
                            _cset(None, ttl=0)
                    except Exception:
                        pass
        except Exception:
            logger.exception("boss_approve_payment: auto-apply of request_payload failed")

        accountant_rows = db.session.query(User.id).filter_by(role="accountant", is_active=True).all()
        for (acc_id,) in accountant_rows:
            create_notification(
                user_id=acc_id,
                type_="PAYMENT_REVIEW_APPROVED",
                message=message,
                related_type="payment_review",
                related_id=review.id,
            )

        cashier_rows = db.session.query(User.id).filter_by(role="cashier", is_active=True).all()
        for (cashier_id,) in cashier_rows:
            create_notification(
                user_id=cashier_id,
                type_="PAYMENT_REVIEW_APPROVED",
                message=(
                    message + " Fungura Cashier -> Approved Requests kugira ngo ushyire mu bikorwa (disburse/collect)."
                ),
                related_type="payment_review",
                related_id=review.id,
            )

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        try:
            app.logger.exception('boss_approve_payment: approval failed for review %s', review_id)
        except Exception:
            pass
        flash("Approval failed. Please refresh and try again.", "danger")
        return redirect(url_for("core.boss_dashboard"))

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

    payload = _safe_payload(getattr(review, "request_payload", None))
    if not payload:
        payload = _safe_payload(review.boss_comment)
    review_type = (review.type or '').strip().lower()
    if review_type == 'loan_disbursement':
        try:
            loan_id = int(payload.get('loan_id') or 0)
        except Exception:
            loan_id = 0
        if loan_id:
            loan = Loan.query.get(loan_id)
            if loan:
                loan.status = 'REJECTED'
                db.session.add(loan)

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


@core_bp.route("/boss/payment_review/<int:review_id>/print")
@role_required("boss", "admin", "accountant")
def print_payment_review(review_id: int):
    """Render a printable approval sheet for a pending or reviewed payment."""

    review = PaymentReview.query.get_or_404(review_id)
    payload = _safe_payload(getattr(review, "request_payload", None))
    amount_breakdown = _review_amount_breakdown(review)
    requested_by = User.query.get(review.created_by_id) if review.created_by_id else None
    approved_by = User.query.get(review.reviewed_by_id) if review.reviewed_by_id else None
    accountant_name = payload.get("accountant_name") or (requested_by.username if requested_by else "")
    cashier_name = payload.get("cashier_name") or ""
    boss_name = approved_by.username if approved_by else ""

    return render_template(
        "receipts/payment_request_form.html",
        review=review,
        payload=payload,
        accountant_name=accountant_name,
        cashier_name=cashier_name,
        boss_name=boss_name,
        requested_by=requested_by,
        approved_by=approved_by,
        amount_breakdown=amount_breakdown,
    )


@core_bp.route("/store/dashboard")
@role_required("store_keeper")
def store_dashboard():
    """Store keeper dashboard focused on bulk output plans and notifications."""
    # Force fresh data reads to avoid stale query results
    db.session.expire_all()


    plans = BulkOutputPlan.query.order_by(
        BulkOutputPlan.created_at.desc()
    ).limit(50).all()

    user_notifications = []
    unread = []
    read = []
    unread_count = 0
    if getattr(current_user, "is_authenticated", False):
        # Fetch notifications via centralized helper to get detailed logs
        try:
            user_notifications, unread_count = fetch_user_notifications(getattr(current_user, 'id', None), unread_limit=20, read_limit=10)
        except Exception:
            app.logger.exception("management.store_dashboard: fetch_user_notifications helper failed; rolling back")
            try:
                db.session.rollback()
            except Exception:
                pass
            user_notifications = []
            unread_count = 0

    return render_template(
        "store/dashboard.html",
        plans=plans,
        notifications=user_notifications,
        unread_notifications_count=unread_count,
    )


@core_bp.route("/store/bulk_plan/<int:plan_id>/execute", methods=["POST"])
@role_required("store_keeper")
def store_execute_bulk_plan(plan_id: int):
    """Store keeper confirms stock release and executes a bulk output plan."""

    plan = BulkOutputPlan.query.get_or_404(plan_id)
    if plan.status != BulkPlanStatus.SENT_TO_STORE.value:
        flash("Only plans waiting for store confirmation can be executed.", "warning")
        return redirect(url_for("core.store_dashboard"))

    plan_rows = plan.plan_json or []
    if not plan_rows:
        flash("This plan has no stock rows to execute.", "danger")
        return redirect(url_for("core.store_dashboard"))

    if plan.mineral_type in {"copper", "coltan"}:
        from copper.models import CopperStock, CopperOutput
        stock_model = CopperStock
        output_model = CopperOutput
        mineral_key = "coltan"
    elif plan.mineral_type == "cassiterite":
        from cassiterite.models import CassiteriteStock, CassiteriteOutput
        stock_model = CassiteriteStock
        output_model = CassiteriteOutput
        mineral_key = "cassiterite"
    else:
        flash("Unsupported mineral type on this plan.", "danger")
        return redirect(url_for("core.store_dashboard"))

    valid_rows = []
    stock_ids = []
    for row in plan_rows:
        if not isinstance(row, dict):
            continue
        sid_raw = row.get("stock_id")
        qty_raw = row.get("planned_output_kg")
        if sid_raw in (None, "", "None"):
            continue
        if qty_raw in (None, "", "None"):
            continue
        try:
            sid_int = int(sid_raw)
            qty_float = float(qty_raw)
        except Exception:
            continue
        if sid_int <= 0 or qty_float <= 0:
            continue
        normalized_row = dict(row)
        normalized_row["stock_id"] = sid_int
        normalized_row["planned_output_kg"] = qty_float
        valid_rows.append(normalized_row)
        stock_ids.append(sid_int)

    if not valid_rows:
        flash("This plan has no valid stock rows to execute. Please ask accountant to recreate the plan.", "danger")
        return redirect(url_for("core.store_dashboard"))
    stocks = stock_model.query.filter(stock_model.id.in_(stock_ids)).all() if stock_ids else []
    stocks_map = {s.id: s for s in stocks}

    for row in valid_rows:
        try:
            sid = int(row.get("stock_id"))
        except Exception:
            sid = 0
        try:
            qty = float(row.get("planned_output_kg") or 0)
        except Exception:
            qty = 0.0
        stock = stocks_map.get(sid)
        if not stock:
            flash(f"Plan row stock {sid} was not found.", "danger")
            return redirect(url_for("core.store_dashboard"))
        if qty <= 0:
            flash(f"Plan row for stock {sid} has invalid quantity.", "danger")
            return redirect(url_for("core.store_dashboard"))
        if qty > float(stock.local_balance or 0):
            flash(
                f"Cannot execute plan: stock {getattr(stock, 'voucher_no', sid)} has only {stock.local_balance} kg available.",
                "danger",
            )
            return redirect(url_for("core.store_dashboard"))

    try:
        for row in valid_rows:
            try:
                sid = int(row.get("stock_id"))
            except Exception:
                sid = 0
            try:
                qty = float(row.get("planned_output_kg") or 0)
            except Exception:
                qty = 0.0
            stock = stocks_map.get(sid)
            if not stock:
                raise ValueError(f"Plan row stock {sid} was not found during execution")
            if qty <= 0:
                raise ValueError(f"Plan row for stock {sid} has invalid quantity during execution")

            quoted_amount = float(row.get("quoted_amount_input") or 0)
            quoted_amount_rwf = float(row.get("quoted_amount_rwf") or 0)
            row_currency = (row.get("currency") or "RWF").upper()
            row_exchange = float(row.get("exchange_rate") or 1.0)

            output_row = output_model(
                stock_id=stock.id,
                date=plan.created_at.date() if plan.created_at else datetime.utcnow().date(),
                output_kg=qty,
                batch_id=plan.batch_id,
                customer=plan.customer,
                output_amount=quoted_amount,
                output_amount_rwf=quoted_amount_rwf,
                amount_paid=0,
                amount_paid_rwf=0,
                currency=row_currency,
                exchange_rate=row_exchange,
                payment_stage="ADVANCE_PENDING",
                note=plan.note,
                voucher_no=getattr(stock, "voucher_no", None),
            )
            output_row.update_debt()

            old_q, old_wp, old_t = stock_model.contribution(stock)
            db.session.add(output_row)
            db.session.flush()

            stock.update_calculations()
            new_q, new_wp, new_t = stock_model.contribution(stock)
            stock_model.apply_aggregate_delta(new_q - old_q, new_wp - old_wp, new_t - old_t)

        plan.status = BulkPlanStatus.STOCK_CONFIRMED.value
        plan.executed_by_id = getattr(current_user, "id", None)
        plan.executed_at = datetime.utcnow()

        active_users = User.query.filter_by(is_active=True).all()
        for u in active_users:
            create_notification(
                user_id=u.id,
                type_="BULK_PLAN_EXECUTED",
                message=(
                    f"Store confirmed and released {mineral_key} batch {plan.batch_id} for customer {plan.customer}. "
                    f"Negotiator can now record advance/installment receipts."
                ),
                related_type="bulk_plan",
                related_id=plan.id,
            )

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        try:
            app.logger.exception('store_execute_bulk_plan: failed to execute plan %s', plan_id)
        except Exception:
            pass
        flash("Failed to execute plan. Please refresh and try again.", "danger")
        return redirect(url_for("core.store_dashboard"))

    flash("Stock confirmed and output executed successfully.", "success")
    return redirect(url_for("core.store_dashboard"))


@core_bp.route("/store/bulk-plan/<int:plan_id>/reject", methods=["POST"])
@role_required("store_keeper")
def store_reject_bulk_plan(plan_id: int):
    """Store keeper rejects a bulk output plan when the stock is not actually available."""

    plan = BulkOutputPlan.query.get_or_404(plan_id)
    if plan.status != BulkPlanStatus.SENT_TO_STORE.value:
        flash("Only plans waiting for store confirmation can be rejected.", "warning")
        return redirect(url_for("core.store_dashboard"))

    reject_reason = (request.form.get("reject_reason") or request.form.get("note") or "").strip()
    if not reject_reason:
        reject_reason = "Stock not found or not available in store"

    plan.status = BulkPlanStatus.CANCELLED.value
    if plan.note:
        plan.note = f"{plan.note} | REJECTED: {reject_reason}"
    else:
        plan.note = f"REJECTED: {reject_reason}"

    try:
        from core.models import create_notification, User

        active_users = User.query.filter_by(is_active=True).all()
        for user in active_users:
            if getattr(user, "role", None) in {"accountant", "boss", "admin"}:
                create_notification(
                    user_id=user.id,
                    type_="BULK_PLAN_REJECTED",
                    message=(
                        f"Store keeper rejected {plan.mineral_type} batch {plan.batch_id}. Reason: {reject_reason}"
                    ),
                    related_type="bulk_plan",
                    related_id=plan.id,
                )
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Failed to reject plan. Please try again.", "danger")
        return redirect(url_for("core.store_dashboard"))

    flash("Bulk output plan rejected.", "warning")
    return redirect(url_for("core.store_dashboard"))


@core_bp.route("/receipts/customer", methods=["GET", "POST"])
@role_required("negotiator", "admin")
def customer_receipts():
    can_record = getattr(current_user, "role", None) in {"negotiator", "admin"}

    if request.method == "POST":
        if not can_record:
            flash("Only negotiator can set customer batch agreements.", "warning")
            return redirect(url_for("core.customer_receipts"))

        plan_id = int(request.form.get("plan_id") or 0)
        plan = BulkOutputPlan.query.get_or_404(plan_id)
        if plan.status not in {BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value}:
            flash("Batch must be stock-confirmed before customer agreement.", "warning")
            return redirect(url_for("core.customer_receipts"))

        submitted_customer = (request.form.get("customer") or "").strip()
        submitted_customer = " ".join(submitted_customer.split())
        if not submitted_customer:
            flash("Customer name is required.", "danger")
            return redirect(url_for("core.customer_receipts"))

        action_type = (request.form.get('action_type') or 'agreement').strip().lower()

        # Advance-only flow: allow recording advance before final agreement is known
        if action_type == 'advance_only':
            try:
                adv_amount_input = float(request.form.get('advance_amount_input') or 0)
            except Exception:
                adv_amount_input = 0.0
            if adv_amount_input <= 0:
                flash('Advance amount must be greater than zero.', 'danger')
                return redirect(url_for('core.customer_receipts'))

            adv_currency = (request.form.get('advance_currency') or 'RWF').upper()
            adv_channel = (request.form.get('advance_payment_channel') or CustomerReceiptChannel.CASH.value).upper()
            adv_note = (request.form.get('advance_note') or '').strip() or None

            try:
                adv_amount_rwf, adv_exchange_rate = _normalize_amount_to_rwf(
                    adv_amount_input,
                    adv_currency,
                    request.form.get('advance_exchange_rate'),
                )
            except Exception as e:
                flash(str(e), 'danger')
                return redirect(url_for('core.customer_receipts'))

            unearned = CustomerUnearnedReceipt(
                mineral_type=_canonical_mineral_type(plan.mineral_type) or plan.mineral_type,
                customer=submitted_customer,
                received_at=datetime.utcnow(),
                payment_channel=adv_channel,
                amount_input=float(adv_amount_input),
                currency=adv_currency,
                exchange_rate=float(adv_exchange_rate or 1.0),
                amount_rwf=float(adv_amount_rwf),
                remaining_rwf=0.0,
                note=adv_note or f'Advance recorded from agreement page for batch {plan.batch_id}',
                proof_image_path=None,
                proof_uploaded_at=None,
                created_by_id=getattr(current_user, 'id', None),
                created_at=datetime.utcnow(),
            )
            db.session.add(unearned)
            db.session.flush()

            alloc = CustomerUnearnedAllocation(
                unearned_id=int(unearned.id),
                batch_id=plan.batch_id,
                stock_mineral_type=_canonical_mineral_type(plan.mineral_type) or plan.mineral_type,
                applied_amount_rwf=float(adv_amount_rwf),
                created_by_id=getattr(current_user, 'id', None),
                created_at=datetime.utcnow(),
            )
            db.session.add(alloc)

            if not plan.customer:
                plan.customer = submitted_customer
                db.session.add(plan)

            boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=int(boss_id),
                    type_='CUSTOMER_UNEARNED_RECORDED',
                    message=(
                        f"Habonetse advance kuri batch {plan.batch_id} ku mukiriya {submitted_customer}: {float(adv_amount_input):,.2f} {adv_currency} (yanditswe na {getattr(current_user, 'username', 'unknown')})."
                    ),
                    related_type='customer_unearned_receipt',
                    related_id=int(unearned.id),
                )

            db.session.commit()
            flash('Advance recorded successfully. Use the handover page to send it to cashier.', 'success')
            return redirect(url_for('core.customer_unearned_receipts'))

        try:
            total_expected_amount = float(request.form.get("total_expected_amount") or 0)
        except ValueError:
            total_expected_amount = 0.0

        if total_expected_amount <= 0:
            # No agreed total yet: fall back to advance-only flow if advance fields are present.
            advance_amount_present = float(request.form.get('advance_amount_input') or 0) > 0
            if advance_amount_present:
                action_type = 'advance_only'
            else:
                flash("No agreed total entered yet. Use Advance Only now, then come back later to set the agreement and deductions.", "warning")
                return redirect(url_for("core.customer_receipts"))

        # Allow negotiator to select agreement currency and provide exchange rate
        currency = (request.form.get("currency") or "RWF").upper()
        try:
            exchange_rate = float(request.form.get('exchange_rate') or 1.0)
            if exchange_rate <= 0:
                exchange_rate = 1.0
        except Exception:
            exchange_rate = 1.0

        # Prevent accidental re-agreement unless user explicitly confirms overwrite.
        force_overwrite = (request.form.get("force_overwrite") or "0").strip() == "1"
        has_existing_agreement = float(plan.total_expected_amount or 0.0) > 0
        if has_existing_agreement and not force_overwrite:
            flash(
                f"Batch {plan.batch_id} already has an agreement ({plan.total_expected_amount:,.2f} RWF). "
                "Tick 'Overwrite existing agreement' to change it.",
                "warning",
            )
            return redirect(url_for("core.customer_receipts"))

        # Capture optional deduction inputs (entered in agreement currency)
        def _floatf(n):
            try:
                return float(request.form.get(n) or 0.0)
            except Exception:
                return 0.0

        deductions = []
        for key, dtype in (('rma_amount', 'RMA'), ('transport_amount', 'TRANSPORT'), ('alex_fee_amount', 'ALEX_FEE'), ('percentage_amount', 'PERCENTAGE')):
            val = _floatf(key)
            if val and val > 0:
                deductions.append({'type': dtype, 'amount': float(val)})

        payload = {
            'action': 'batch_agreement',
            'plan_id': int(plan.id),
            'batch_id': plan.batch_id,
            'mineral_type': plan.mineral_type,
            'customer': submitted_customer,
            'total_expected_amount': float(total_expected_amount),
            'currency': currency,
            'exchange_rate': float(exchange_rate),
            'deductions': deductions,
        }
        review = PaymentReview(
            mineral_type=(plan.mineral_type or None),
            type='batch_agreement',
            customer=submitted_customer,
            amount=float(total_expected_amount),
            currency=currency,
            created_by_id=getattr(current_user, 'id', None),
            status=PaymentReviewStatus.PENDING_REVIEW.value,
            request_payload=json.dumps(payload),
        )
        db.session.add(review)
        db.session.flush()

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='BATCH_AGREEMENT_REQUESTED',
                message=(
                    f"Negotiator {getattr(current_user, 'username', 'unknown')} arasaba kwemezwa deal ya batch {plan.batch_id} "
                    f"({(plan.mineral_type or '').upper()}), umukiriya: {submitted_customer}, igiciro cyose: {total_expected_amount:,.2f} {currency}."
                ),
                related_type='payment_review',
                related_id=int(review.id),
            )

        db.session.commit()
        flash('Ubusabe bwo kwemeza deal (agreement) bwoherejwe ku Muyobozi.', 'success')
        return redirect(url_for('core.customer_receipts'))

    plans = (
        BulkOutputPlan.query
        .filter(BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
                BulkOutputPlan.mineral_type.in_(_mineral_aliases('copper') + _mineral_aliases('cassiterite')))
        .order_by(BulkOutputPlan.created_at.desc())
        .limit(100)
        .all()
    )
    db.session.expire_all()

    plans_view = []
    for plan in plans:
        summary = _compute_plan_averages(plan)
        plan_currency = (getattr(plan, 'currency', None) or 'RWF').upper()
        plan_rate = float(getattr(plan, 'exchange_rate', 1.0) or 1.0)
        remaining_rwf = _batch_outstanding_rwf(plan.mineral_type, plan.batch_id)
        remaining_display = remaining_rwf
        if plan_currency == 'USD' and plan_rate > 0:
            remaining_display = float(remaining_rwf / plan_rate)
        plans_view.append({
            "id": plan.id,
            "mineral_type": plan.mineral_type,
            "customer": plan.customer,
            "batch_id": plan.batch_id,
            "status": plan.status,
            "created_at": plan.created_at,
            "summary": summary,
            "total_expected_amount": float(plan.total_expected_amount or 0.0),
            "currency": plan_currency,
            "exchange_rate": plan_rate,
            "remaining_rwf": float(remaining_rwf),
            "remaining_display": float(remaining_display),
        })

    receipts = CustomerReceipt.query.options(joinedload(CustomerReceipt.created_by)).order_by(CustomerReceipt.received_at.desc()).limit(120).all()
    return render_template("negotiator/customer_receipts.html", plans=plans_view, receipts=receipts, can_record=can_record)


@core_bp.route('/receipts/customer-unearned', methods=['GET', 'POST'])
@role_required('negotiator', 'admin')
def customer_unearned_receipts():
    can_record = getattr(current_user, 'role', None) in {'negotiator', 'admin'}
    if request.method == 'POST':
        if not can_record:
            flash('Only negotiator can record customer unearned receipts.', 'warning')
            return redirect(url_for('core.customer_unearned_receipts'))

        customer = (request.form.get('customer') or '').strip()
        customer = ' '.join(customer.split())
        if not customer:
            flash('Customer name is required.', 'danger')
            return redirect(url_for('core.customer_unearned_receipts'))

        mineral_type = (request.form.get('mineral_type') or '').strip().lower() or None
        payment_channel = (request.form.get('payment_channel') or CustomerReceiptChannel.CASH.value).upper()
        currency = (request.form.get('currency') or 'RWF').upper()
        try:
            amount_input = float(request.form.get('amount_input') or 0.0)
        except Exception:
            amount_input = 0.0
        if amount_input <= 0:
            flash('Amount must be > 0.', 'danger')
            return redirect(url_for('core.customer_unearned_receipts'))

        try:
            exchange_rate_input = float(request.form.get('exchange_rate') or 1.0)
        except Exception:
            exchange_rate_input = 1.0

        note = (request.form.get('note') or '').strip() or None
        confirm_new_customer = (request.form.get('confirm_new_customer') or '').strip().lower() in {'1', 'true', 'yes', 'on'}

        try:
            existing_names = [r[0] for r in db.session.query(CustomerReceipt.customer).filter(CustomerReceipt.customer.isnot(None)).all()]
            existing_names += [r[0] for r in db.session.query(CustomerUnearnedReceipt.customer).filter(CustomerUnearnedReceipt.customer.isnot(None)).all()]
            existing_names += [r[0] for r in db.session.query(BulkOutputPlan.customer).filter(BulkOutputPlan.customer.isnot(None)).all()]
        except Exception:
            existing_names = []

        norm_customer = normalize_counterparty_name(customer)
        exact_customer_exists = any(normalize_counterparty_name(name) == norm_customer for name in existing_names)
        if not exact_customer_exists:
            close_matches = close_name_matches(customer, existing_names, limit=5, cutoff=0.86)
            if close_matches and not confirm_new_customer:
                flash(
                    f"Customer name looks similar to existing customer(s): {', '.join(close_matches[:3])}. Select the existing customer or confirm this is a new customer.",
                    'warning',
                )
                return redirect(url_for('core.customer_unearned_receipts'))

        try:
            row = _create_customer_unearned_receipt(
                customer=customer,
                mineral_type=mineral_type,
                amount_input=amount_input,
                currency=currency,
                exchange_rate_input=exchange_rate_input,
                payment_channel=payment_channel,
                note=note,
                batch_id=(request.form.get('batch_id') or '').strip() or None,
            )
        except Exception as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('core.customer_unearned_receipts'))

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='CUSTOMER_UNEARNED_RECORDED',
                message=(
                    f"Habonetse amafaranga y'umukiriya atarakoreshwa (unearned): {customer} "
                    f"yatanze {float(row.amount_rwf or 0):,.2f} RWF."
                ),
                related_type='customer_unearned_receipt',
                related_id=int(row.id),
            )

        db.session.commit()
        flash('Unearned receipt recorded. Cashier must collect if CASH.', 'success')
        return redirect(url_for('core.customer_unearned_receipts'))

    rows = (
        CustomerUnearnedReceipt.query
        .order_by(CustomerUnearnedReceipt.received_at.desc())
        .limit(200)
        .all()
    )
    return render_template('negotiator/customer_unearned_receipts.html', rows=rows, can_record=can_record)


@core_bp.route('/api/customers/autocomplete')
@role_required('negotiator', 'accountant', 'boss', 'admin', 'cashier')
def customers_autocomplete():
    q = (request.args.get('q') or '').strip()
    if not q:
        return safe_jsonify({'results': []})

    q_norm = ' '.join(q.lower().split())

    results = []
    seen = set()
    try:
        # Prefer existing customer receipt names (most canonical)
        rows = (
            db.session.query(CustomerReceipt.customer)
            .filter(func.lower(CustomerReceipt.customer).contains(q_norm))
            .distinct()
            .order_by(CustomerReceipt.customer.asc())
            .limit(10)
            .all()
        )
        for (nm,) in rows:
            if nm and nm.lower() not in seen:
                results.append(nm)
                seen.add(nm.lower())
    except Exception:
        results = results

    try:
        rows = (
            db.session.query(CustomerUnearnedReceipt.customer)
            .filter(func.lower(CustomerUnearnedReceipt.customer).contains(q_norm))
            .distinct()
            .order_by(CustomerUnearnedReceipt.customer.asc())
            .limit(10)
            .all()
        )
        for (nm,) in rows:
            if nm and nm.lower() not in seen:
                results.append(nm)
                seen.add(nm.lower())
    except Exception:
        results = results

    try:
        rows = (
            db.session.query(BulkOutputPlan.customer)
            .filter(BulkOutputPlan.customer.isnot(None), func.lower(BulkOutputPlan.customer).contains(q_norm))
            .distinct()
            .order_by(BulkOutputPlan.customer.asc())
            .limit(10)
            .all()
        )
        for (nm,) in rows:
            if nm and nm.lower() not in seen:
                results.append(nm)
                seen.add(nm.lower())
    except Exception:
        results = results

    return safe_jsonify({'results': results[:15]})


@core_bp.route('/api/batches/autocomplete')
@role_required('negotiator', 'accountant', 'boss', 'admin', 'cashier')
def batches_autocomplete():
    q = (request.args.get('q') or '').strip()
    if not q:
        return safe_jsonify({'results': []})
    q_norm = ' '.join(q.lower().split())
    results = []
    seen = set()
    try:
        rows = (
            db.session.query(BulkOutputPlan.batch_id)
            .filter(BulkOutputPlan.batch_id.isnot(None), func.lower(BulkOutputPlan.batch_id).contains(q_norm))
            .distinct()
            .order_by(BulkOutputPlan.batch_id.asc())
            .limit(15)
            .all()
        )
        for (b,) in rows:
            if b and b.lower() not in seen:
                results.append(b)
                seen.add(b.lower())
    except Exception:
        pass

    try:
        rows = (
            db.session.query(CustomerUnearnedAllocation.batch_id)
            .filter(CustomerUnearnedAllocation.batch_id.isnot(None), func.lower(CustomerUnearnedAllocation.batch_id).contains(q_norm))
            .distinct()
            .order_by(CustomerUnearnedAllocation.batch_id.asc())
            .limit(15)
            .all()
        )
        for (b,) in rows:
            if b and b.lower() not in seen:
                results.append(b)
                seen.add(b.lower())
    except Exception:
        pass

    return safe_jsonify({'results': results})


@core_bp.route('/receipts/customer/<int:receipt_id>/handover', methods=['POST'])
@role_required('negotiator', 'admin')
def negotiator_handover_customer_receipt(receipt_id: int):
    row = CustomerReceipt.query.get_or_404(receipt_id)
    if (row.payment_channel or '').upper() != 'CASH':
        flash('Only CASH receipts require handover to cashier.', 'warning')
        return redirect(request.referrer or url_for('core.update_debts'))
    if row.is_collected:
        flash('Receipt already collected by cashier.', 'info')
        return redirect(request.referrer or url_for('core.update_debts'))
    if getattr(row, 'is_handed_over', False):
        flash('Receipt already handed over to cashier.', 'info')
        return redirect(request.referrer or url_for('core.update_debts'))

    row.is_handed_over = True
    row.handed_over_by_id = getattr(current_user, 'id', None)
    row.handed_over_at = datetime.utcnow()
    db.session.add(row)

    cashier_rows = db.session.query(User.id).filter_by(role='cashier', is_active=True).all()
    for (cashier_id,) in cashier_rows:
        create_notification(
            user_id=int(cashier_id),
            type_='CASH_RECEIPT_HANDED_OVER',
            message=(
                f"Negotiator {getattr(current_user, 'username', 'unknown')} yohereje receipt #{row.id}: "
                f"{float(row.amount_input or 0):,.2f} {row.currency or 'RWF'} (rate {float(row.exchange_rate or 1.0):.4f}) ~ {float(row.amount_rwf or 0):,.2f} RWF) ngo Umubitsi ayabarure kandi ayashyire kuri konti."
            ),
            related_type='customer_receipt',
            related_id=int(row.id),
        )

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        create_notification(
            user_id=int(boss_id),
            type_='CASH_RECEIPT_HANDED_OVER',
            message=(
                f"Negotiator {getattr(current_user, 'username', 'unknown')} yohereje receipt #{row.id} ku mubitsi (handover): "
                f"{float(row.amount_input or 0):,.2f} {row.currency or 'RWF'} (rate {float(row.exchange_rate or 1.0):.4f}) ~ {float(row.amount_rwf or 0):,.2f} RWF."
            ),
            related_type='customer_receipt',
            related_id=int(row.id),
        )

    db.session.commit()
    flash('Wohereje amafaranga ku Mubitsi (handover).', 'success')
    return redirect(request.referrer or url_for('core.update_debts'))


@core_bp.route('/receipts/customer-unearned/<int:unearned_id>/handover', methods=['POST'])
@role_required('negotiator', 'admin')
def negotiator_handover_customer_unearned(unearned_id: int):
    row = CustomerUnearnedReceipt.query.get_or_404(unearned_id)
    if (row.payment_channel or '').upper() != 'CASH':
        flash('Only CASH unearned receipts require handover to cashier.', 'warning')
        return redirect(request.referrer or url_for('core.customer_unearned_receipts'))
    if row.is_collected:
        flash('Unearned receipt already collected by cashier.', 'info')
        return redirect(request.referrer or url_for('core.customer_unearned_receipts'))
    if getattr(row, 'is_handed_over', False):
        flash('Unearned receipt already handed over to cashier.', 'info')
        return redirect(request.referrer or url_for('core.customer_unearned_receipts'))

    row.is_handed_over = True
    row.handed_over_by_id = getattr(current_user, 'id', None)
    row.handed_over_at = datetime.utcnow()
    db.session.add(row)

    cashier_rows = db.session.query(User.id).filter_by(role='cashier', is_active=True).all()
    for (cashier_id,) in cashier_rows:
        create_notification(
            user_id=int(cashier_id),
            type_='UNEARNED_CASH_HANDED_OVER',
            message=(
                f"Negotiator {getattr(current_user, 'username', 'unknown')} yohereje unearned #{row.id}: "
                f"{float(row.amount_input or 0):,.2f} {row.currency or 'RWF'} (rate {float(row.exchange_rate or 1.0):.4f}) ~ {float(row.amount_rwf or 0):,.2f} RWF) ngo Umubitsi ayabarure kandi ayashyire kuri konti."
            ),
            related_type='customer_unearned_receipt',
            related_id=int(row.id),
        )

    boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
    for (boss_id,) in boss_rows:
        create_notification(
            user_id=int(boss_id),
            type_='UNEARNED_CASH_HANDED_OVER',
            message=(
                f"Negotiator {getattr(current_user, 'username', 'unknown')} yohereje unearned #{row.id} ku mubitsi (handover): "
                f"{float(row.amount_input or 0):,.2f} {row.currency or 'RWF'} (rate {float(row.exchange_rate or 1.0):.4f}) ~ {float(row.amount_rwf or 0):,.2f} RWF."
            ),
            related_type='customer_unearned_receipt',
            related_id=int(row.id),
        )

    db.session.commit()
    flash('Wohereje amafaranga ku Mubitsi (handover).', 'success')
    return redirect(request.referrer or url_for('core.customer_unearned_receipts'))


@core_bp.route('/receipts/customer/<int:receipt_id>', methods=['GET'])
@role_required('negotiator', 'accountant', 'cashier', 'boss', 'admin')
def customer_receipt_detail(receipt_id: int):
    row = CustomerReceipt.query.options(joinedload(CustomerReceipt.created_by)).get_or_404(receipt_id)
    return render_template('receipts/customer_receipt_detail.html', receipt=row)


@core_bp.route('/receipts/customer-unearned/<int:unearned_id>', methods=['GET'])
@role_required('negotiator', 'accountant', 'cashier', 'boss', 'admin')
def customer_unearned_receipt_detail(unearned_id: int):
    row = CustomerUnearnedReceipt.query.options(joinedload(CustomerUnearnedReceipt.created_by)).get_or_404(unearned_id)
    return render_template('receipts/customer_unearned_receipt_detail.html', receipt=row)


@core_bp.route('/accountant/customer-unearned/<int:unearned_id>/allocate', methods=['POST'])
@role_required('negotiator', 'admin')
def allocate_customer_unearned(unearned_id: int):
    row = CustomerUnearnedReceipt.query.get_or_404(unearned_id)
    if float(row.remaining_rwf or 0.0) <= 0.0:
        flash('This unearned receipt has no remaining balance.', 'warning')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))

    batch_id = (request.form.get('batch_id') or '').strip()
    mineral_type = (request.form.get('mineral_type') or '').strip().lower()
    if not batch_id or not mineral_type:
        flash('Batch and mineral type are required.', 'danger')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))

    try:
        amount_rwf = float(request.form.get('amount_rwf') or 0.0)
    except Exception:
        amount_rwf = 0.0
    if amount_rwf <= 0:
        flash('Allocation amount must be > 0.', 'danger')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))
    if amount_rwf > float(row.remaining_rwf or 0.0):
        flash('Allocation amount exceeds remaining unearned balance.', 'danger')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))

    # Validate that batch exists (agreement already created)
    aliases = _mineral_aliases(_canonical_mineral_type(mineral_type))
    plan = (
        BulkOutputPlan.query
        .filter(
            BulkOutputPlan.batch_id == batch_id,
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.total_expected_amount.isnot(None),
            BulkOutputPlan.total_expected_amount > 0,
        )
        .first()
    )
    if not plan:
        flash('Batch agreement not found for that batch/mineral.', 'danger')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))

    try:
        # Apply to batch debt by creating a real CustomerReceipt (credit) referencing this unearned row.
        applied = _apply_receipt_to_batch(mineral_type, batch_id, float(amount_rwf), CustomerReceiptType.ADVANCE.value)
        if applied <= 0:
            flash('Allocation would exceed batch agreement. Reduce amount.', 'danger')
            return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))

        alloc = CustomerUnearnedAllocation(
            unearned_id=int(row.id),
            stock_mineral_type=_canonical_mineral_type(mineral_type),
            batch_id=batch_id,
            applied_amount_rwf=float(applied),
            created_by_id=getattr(current_user, 'id', None),
            created_at=datetime.utcnow(),
            note=f"Allocated to batch {batch_id}",
        )
        row.remaining_rwf = float(row.remaining_rwf or 0.0) - float(applied)
        db.session.add(alloc)
        db.session.add(row)

        receipt = CustomerReceipt(
            mineral_type=_canonical_mineral_type(mineral_type),
            batch_id=batch_id,
            customer=' '.join((row.customer or '').split()),
            bulk_plan_id=getattr(plan, 'id', None),
            received_at=datetime.utcnow(),
            receipt_type=CustomerReceiptType.ADVANCE.value,
            payment_channel=row.payment_channel,
            amount_input=float(applied),
            currency='RWF',
            exchange_rate=1.0,
            amount_rwf=float(applied),
            created_by_id=getattr(current_user, 'id', None),
            created_at=datetime.utcnow(),
            note=f"From unearned receipt #{row.id}",
            proof_image_path=None,
            proof_uploaded_at=None,
        )
        db.session.add(receipt)

        boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
        for (boss_id,) in boss_rows:
            create_notification(
                user_id=int(boss_id),
                type_='CUSTOMER_UNEARNED_ALLOCATED',
                message=(
                    f"Amafaranga y'umukiriya {row.customer} yari unearned yashyizwe kuri batch {batch_id}: "
                    f"{float(applied):,.2f} RWF."
                ),
                related_type='customer_receipt',
                related_id=int(getattr(receipt, 'id', 0) or 0),
            )

        db.session.commit()
        flash('Unearned receipt allocated to batch and customer ledger updated.', 'success')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger', customer=row.customer))
    except Exception as e:
        db.session.rollback()
        flash(f'Allocation failed: {e}', 'danger')
        return redirect(request.referrer or url_for('core.consolidated_customer_ledger_index'))


@core_bp.route('/accountant/customer-unearned', methods=['GET'])
@role_required('negotiator', 'admin')
def accountant_customer_unearned_index():
    rows = (
        CustomerUnearnedReceipt.query
        .order_by(CustomerUnearnedReceipt.received_at.desc())
        .limit(300)
        .all()
    )
    return render_template('accountant/unearned_receipts.html', rows=rows)


def _plan_summary(plan: BulkOutputPlan) -> dict:
    """Extract batch metadata with O(1) complexity from plan_json."""
    plan_rows = plan.plan_json or []
    total_qty = 0.0

    metadata = {}
    data_rows = plan_rows
    if plan_rows and isinstance(plan_rows[0], dict):
        first_row = plan_rows[0]
        if "achieved_moyenne" in first_row and "planned_output_kg" not in first_row:
            metadata = first_row
            data_rows = plan_rows[1:]

    for row in data_rows:
        try:
            qty = float((row or {}).get("planned_output_kg") if isinstance(row, dict) else getattr(row, "planned_output_kg", 0) or 0)
            if qty > 0:
                total_qty += qty
        except Exception:
            continue

    return {
        "total_qty": total_qty,
        "moyenne": float(metadata.get("achieved_moyenne", 0.0)) if metadata.get("achieved_moyenne") is not None else None,
        "moyenne_nb": float(metadata.get("achieved_moyenne_nb", 0.0)) if metadata.get("achieved_moyenne_nb") is not None else None,
    }


def _compute_plan_averages(plan: BulkOutputPlan) -> dict:
    """Return safe averages for a plan.

    Source of truth:
    - Prefer stored metadata in plan_json[0] (achieved_moyenne / achieved_moyenne_nb)
    - If missing/zero, recompute from plan_json line items + stock rows

    NOTE: moyenne/moyenne_nb here represent *quality averages* (not price).
    """
    summary = _plan_summary(plan)
    if summary.get("moyenne") is not None and summary.get("moyenne") != 0:
        return summary

    plan_rows = plan.plan_json or []
    data_rows = plan_rows
    if plan_rows and isinstance(plan_rows[0], dict):
        first_row = plan_rows[0]
        if "achieved_moyenne" in first_row and "planned_output_kg" not in first_row:
            data_rows = plan_rows[1:]

    # Compute weighted averages from selected stock lines
    total_qty = 0.0
    weighted_percent = 0.0
    weighted_nb = 0.0
    stock_ids = []
    lines = []
    for row in data_rows:
        if not isinstance(row, dict):
            continue
        try:
            stock_id = row.get("stock_id")
            qty = float(row.get("planned_output_kg") or 0)
        except Exception:
            continue
        if not stock_id or qty <= 0:
            continue
        stock_ids.append(int(stock_id))
        lines.append((int(stock_id), float(qty)))
        total_qty += float(qty)

    if total_qty <= 0 or not stock_ids:
        return {"total_qty": float(summary.get("total_qty") or 0.0), "moyenne": 0.0, "moyenne_nb": 0.0}

    stock_model = _get_stock_model(plan.mineral_type)
    if not stock_model:
        return {"total_qty": total_qty, "moyenne": 0.0, "moyenne_nb": 0.0}

    try:
        stock_rows = stock_model.query.filter(stock_model.id.in_(list(set(stock_ids)))).all()
        stock_map = {int(getattr(s, "id")): s for s in stock_rows}
    except Exception:
        logger.exception("_compute_plan_averages: failed loading stocks for plan_id=%s", getattr(plan, 'id', None))
        return {"total_qty": total_qty, "moyenne": 0.0, "moyenne_nb": 0.0}

    for stock_id, qty in lines:
        s = stock_map.get(int(stock_id))
        if not s:
            continue
        try:
            pct = float(getattr(s, "percentage", 0) or 0.0)
        except Exception:
            pct = 0.0

        # Prefer explicit NB field when available (copper). Otherwise derive an
        # NB-like value from t_unity/local_balance (cassiterite).
        nb_val = None
        try:
            nb_val = getattr(s, "nb", None)
        except Exception:
            nb_val = None

        if nb_val is None:
            try:
                t_unity = float(getattr(s, "t_unity", 0) or 0.0)
                bal = float(getattr(s, "local_balance", 0) or 0.0)
                nb_val = (t_unity / bal) if bal else 0.0
            except Exception:
                nb_val = 0.0
        else:
            try:
                nb_val = float(nb_val or 0.0)
            except Exception:
                nb_val = 0.0

        weighted_percent += pct * qty
        weighted_nb += nb_val * qty

    moyenne = float(weighted_percent / total_qty) if total_qty else 0.0
    moyenne_nb = float(weighted_nb / total_qty) if total_qty else 0.0
    return {"total_qty": total_qty, "moyenne": moyenne, "moyenne_nb": moyenne_nb}


@core_bp.route('/api/plan_averages/<int:plan_id>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def api_plan_averages(plan_id: int):
    plan = BulkOutputPlan.query.get_or_404(plan_id)
    data = _compute_plan_averages(plan)
    return safe_jsonify({
        "total_qty": float(data.get("total_qty") or 0.0),
        "moyenne": float(data.get("moyenne") or 0.0),
        "moyenne_nb": float(data.get("moyenne_nb") or 0.0),
    })


def _customers_for_mineral(mineral_type: str) -> list[str]:
    mineral = _canonical_mineral_type(mineral_type)
    if mineral_type == 'all' or not mineral:
        aliases = ('copper', 'coltan', 'cassiterite')
    else:
        aliases = _mineral_aliases(mineral)
    if not aliases:
        return []

    plan_rows = (
        db.session.query(BulkOutputPlan.customer)
        .filter(
            BulkOutputPlan.customer.isnot(None),
            BulkOutputPlan.mineral_type.in_(aliases),
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        )
        .distinct()
        .all()
    )
    receipt_rows = (
        db.session.query(CustomerReceipt.customer)
        .filter(CustomerReceipt.customer.isnot(None), CustomerReceipt.mineral_type.in_(aliases))
        .distinct()
        .all()
    )
    unearned_rows = (
        db.session.query(CustomerUnearnedReceipt.customer)
        .filter(CustomerUnearnedReceipt.customer.isnot(None), CustomerUnearnedReceipt.mineral_type.in_(aliases))
        .distinct()
        .all()
    )

    names = set()
    for row in plan_rows + receipt_rows + unearned_rows:
        name = (row[0] if row else '') or ''
        name = name.strip()
        if name:
            names.add(name)

    return sorted(names, key=lambda n: n.lower())


def _to_base_currency(amount_input: float | None, amount_rwf: float | None, row_currency: str | None, row_rate: float | None, base_currency: str | None, base_rate: float | None) -> float:
    row_currency = (row_currency or base_currency or 'RWF').upper()
    base_currency = (base_currency or 'RWF').upper()
    row_rate = float(row_rate or 1.0)
    base_rate = float(base_rate or 1.0)
    amount_input_val = float(amount_input or 0.0)
    amount_rwf_val = float(amount_rwf or 0.0)

    if base_currency == 'USD':
        if row_currency == 'USD':
            return amount_input_val
        if base_rate > 0:
            return amount_rwf_val / base_rate
        if row_rate > 0:
            return amount_input_val
        return amount_rwf_val

    if row_currency == 'USD':
        if row_rate > 0:
            return amount_input_val * row_rate
        return amount_rwf_val

    return amount_rwf_val if amount_rwf_val > 0 else amount_input_val


def _customer_batch_cards(mineral_type: str, customer: str) -> list[dict]:
    mineral = _canonical_mineral_type(mineral_type)
    aliases = _mineral_aliases(mineral)
    customer_name = (customer or '').strip()
    if not aliases or not customer_name:
        return []

    plans = (
        BulkOutputPlan.query
        .filter(BulkOutputPlan.customer == customer_name, BulkOutputPlan.mineral_type.in_(aliases))
        .order_by(BulkOutputPlan.created_at.asc(), BulkOutputPlan.id.asc())
        .all()
    )
    receipts = (
        CustomerReceipt.query
        .filter(CustomerReceipt.customer == customer_name, CustomerReceipt.mineral_type.in_(aliases))
        .order_by(CustomerReceipt.received_at.asc(), CustomerReceipt.id.asc())
        .all()
    )
    # Include deductions (expenses) for the customer by joining through the plan row.
    deductions = (
        BatchDeduction.query
        .join(BulkOutputPlan, BatchDeduction.batch_id == BulkOutputPlan.id)
        .filter(
            BulkOutputPlan.customer == customer_name,
            BulkOutputPlan.mineral_type.in_(aliases),
        )
        .order_by(BatchDeduction.created_at.asc())
        .all()
    )
    # Include allocations from unearned receipts so batches with allocated advances show up
    allocations = (
        CustomerUnearnedAllocation.query
        .join(CustomerUnearnedReceipt, CustomerUnearnedAllocation.unearned_id == CustomerUnearnedReceipt.id)
        .filter(CustomerUnearnedReceipt.customer == customer_name, CustomerUnearnedAllocation.stock_mineral_type.in_(aliases))
        .order_by(CustomerUnearnedAllocation.created_at.asc())
        .all()
    )

    batches = set()
    for p in plans:
        if p.batch_id:
            batches.add(p.batch_id)
    for r in receipts:
        if r.batch_id:
            batches.add(r.batch_id)
    for d in deductions:
        # d.batch_id is INTEGER FK to plan.id, get the actual string batch_id from plan
        if d.plan and d.plan.batch_id:
            batches.add(d.plan.batch_id)
    for a in allocations:
        if a.batch_id:
            batches.add(a.batch_id)

    cards = []
    for batch_id in sorted(batches):
        plans_for_batch = [p for p in plans if p.batch_id == batch_id]
        receipts_for_batch = [r for r in receipts if r.batch_id == batch_id]
        deductions_for_batch = [d for d in deductions if getattr(d.plan, 'batch_id', None) == batch_id]
        allocations_for_batch = [a for a in allocations if a.batch_id == batch_id]

        latest_plan = plans_for_batch[-1] if plans_for_batch else None
        base_currency = (getattr(latest_plan, 'currency', None) or 'RWF').upper() if latest_plan else 'RWF'
        base_rate = float(getattr(latest_plan, 'exchange_rate', 1.0) or 1.0) if latest_plan else 1.0

        expected = float(sum(float(p.total_expected_amount or 0.0) for p in plans_for_batch))
        deducted = float(sum(
            _to_base_currency(d.amount_input, d.amount_rwf, d.currency, d.exchange_rate, base_currency, base_rate)
            for d in deductions_for_batch
        ))
        paid = float(sum(
            _to_base_currency(r.amount_input, r.amount_rwf, r.currency, r.exchange_rate, base_currency, base_rate)
            for r in receipts_for_batch
        ))
        # Include applied unearned allocations as part of paid amounts
        paid += float(sum(
            _to_base_currency(a.applied_amount_rwf, a.applied_amount_rwf, 'RWF', 1.0, base_currency, base_rate)
            for a in allocations_for_batch
        ))
        remaining = float(max(expected - deducted - paid, 0.0))
        remaining_rwf = float(remaining * base_rate if base_currency == 'USD' else remaining)

        summary = _compute_plan_averages(latest_plan) if latest_plan else {}
        
        # Determine mineral type from any available source
        mineral_type_for_batch = None
        if plans_for_batch:
            mineral_type_for_batch = plans_for_batch[0].mineral_type
        elif receipts_for_batch:
            mineral_type_for_batch = receipts_for_batch[0].mineral_type
        elif deductions_for_batch:
            mineral_type_for_batch = deductions_for_batch[0].mineral_type
        elif allocations_for_batch:
            mineral_type_for_batch = allocations_for_batch[0].stock_mineral_type

        cards.append({
            'batch_id': batch_id,
            'mineral_type': mineral_type_for_batch or 'unknown',
            'expected': expected,
            'paid': paid,
            'remaining': remaining,
            'remaining_rwf': remaining_rwf,
            'base_currency': base_currency,
            'base_exchange_rate': base_rate,
            'qty': float(summary.get('total_qty') or 0.0),
            'moyenne': float(summary.get('moyenne') or 0.0),
            'moyenne_nb': float(summary.get('moyenne_nb') or 0.0),
        })

    return cards


def _customer_ledger_data(mineral_type: str, customer: str, batch_id: str | None = None, page: int | None = None, per_page: int | None = None, filter_from=None, filter_to=None):
    """Assemble complete customer ledger from SINGLE SOURCE OF TRUTH.
    
    ╔════════════════════════════════════════════════════════════════════╗
    ║ UNIFIED LEDGER SOURCE: BulkOutputPlan + CustomerReceipt           ║
    ║ NO DUPLICATE QUERIES, NO FALLBACKS                                 ║
    ╚════════════════════════════════════════════════════════════════════╝
    
    Purpose:
        Build debit/credit ledger for a customer showing:
        - DEBIT entries: BulkOutputPlan rows (agreed amounts)
        - CREDIT entries: CustomerReceipt rows (payments made)
        - Running balance: outstanding per batch
    
    Args:
        mineral_type: 'copper'|'coltan'|'cassiterite' (user input)
        customer: Customer name (exact match)
        batch_id: Optional - filter to specific batch
    
    Returns:
        (ledger, total_expected, total_paid, remaining)
        where ledger = [
            {
                date: datetime,
                description: str (Agreement or Payment),
                debit: float (amount owed),
                credit: float (amount paid),
                balance: float (running balance),
                batch_id: str,
                mineral_type: str,
                qty: float (kg),
                moyenne: float (%),
                moyenne_nb: float (%),
            },
            ...
        ]
    
    Data Pipeline:
        1. Normalize mineral_type → canonical form (coltan→copper)
        2. Query BulkOutputPlan WHERE customer + mineral_type
        3. Query CustomerReceipt WHERE customer + mineral_type
        4. Build ledger entries: debit (agreements) + credit (payments)
        5. Calculate running balance (total_expected - cumulative_paid)
    
    Outstanding Calculation:
        remaining = SUM(plans.total_expected_amount) - SUM(receipts.amount_rwf)
    
    Used by:
        - copper_customer_ledger_batch() - Negotiator view (edit buttons)
        - cassiterite_customer_ledger_batch() - Negotiator view (edit buttons)
        - boss_copper_customer_ledger() - Boss view (read-only)
        - boss_cassiterite_customer_ledger() - Boss view (read-only)
    """
    customer_name = (customer or '').strip()
    if not customer_name:
        abort(404)

    mineral = _canonical_mineral_type(mineral_type)
    aliases = _mineral_aliases(mineral)
    if not aliases:
        abort(404)

    batch = (batch_id or '').strip() or None
    if filter_from:
        from_dt = datetime.combine(filter_from, time.min)
    else:
        from_dt = None
    if filter_to:
        to_dt = datetime.combine(filter_to, time.max)
    else:
        to_dt = None

    # Build DB-level queries for plans (debits) and receipts (credits).
    # We UNION ALL them with explicit labeled columns and typed NULLs so PostgreSQL
    # can reconcile integer/text columns across all branches.
    typed_int_null = cast(literal(None), Integer)
    typed_text_null = cast(literal(None), String)
    typed_float_null = cast(literal(None), Float)
    plans_stmt = (
        select(
            BulkOutputPlan.created_at.label('date'),
            literal(1).label('sort_key'),
            literal('plan').label('entry_kind'),
            BulkOutputPlan.id.label('plan_id'),
            typed_int_null.label('receipt_id'),
            BulkOutputPlan.batch_id.label('batch_id'),
            BulkOutputPlan.total_expected_amount.label('debit'),
            literal(0.0).label('credit'),
            BulkOutputPlan.total_expected_amount.label('original_amount'),
            BulkOutputPlan.currency.label('original_currency'),
            BulkOutputPlan.exchange_rate.label('original_exchange_rate'),
            (BulkOutputPlan.total_expected_amount * BulkOutputPlan.exchange_rate).label('debit_rwf'),
            literal(0.0).label('credit_rwf'),
            typed_text_null.label('proof_path'),
            literal('AGREEMENT').label('detail'),
        )
        .where(
            BulkOutputPlan.customer == customer_name,
            BulkOutputPlan.mineral_type.in_(aliases),
        )
    )
    if from_dt:
        plans_stmt = plans_stmt.where(BulkOutputPlan.created_at >= from_dt)
    if to_dt:
        plans_stmt = plans_stmt.where(BulkOutputPlan.created_at <= to_dt)

    deductions_stmt = (
        select(
            BatchDeduction.created_at.label('date'),
            literal(2).label('sort_key'),
            literal('deduction').label('entry_kind'),
            BulkOutputPlan.id.label('plan_id'),
            typed_int_null.label('receipt_id'),
            BulkOutputPlan.batch_id.label('batch_id'),
            literal(0.0).label('debit'),
            BatchDeduction.amount_input.label('credit'),
            BatchDeduction.amount_input.label('original_amount'),
            BatchDeduction.currency.label('original_currency'),
            BatchDeduction.exchange_rate.label('original_exchange_rate'),
            literal(0.0).label('debit_rwf'),
            func.coalesce(BatchDeduction.amount_rwf, 0).label('credit_rwf'),
            typed_text_null.label('proof_path'),
            BatchDeduction.deduction_type.label('detail'),
        )
        .select_from(BatchDeduction.__table__.join(BulkOutputPlan, BatchDeduction.batch_id == BulkOutputPlan.id))
        .where(
            BulkOutputPlan.customer == customer_name,
            BulkOutputPlan.mineral_type.in_(aliases),
        )
    )
    if from_dt:
        deductions_stmt = deductions_stmt.where(BatchDeduction.created_at >= from_dt)
    if to_dt:
        deductions_stmt = deductions_stmt.where(BatchDeduction.created_at <= to_dt)

    receipts_stmt = (
        select(
            CustomerReceipt.received_at.label('date'),
            literal(3).label('sort_key'),
            literal('receipt').label('entry_kind'),
            typed_int_null.label('plan_id'),
            CustomerReceipt.id.label('receipt_id'),
            CustomerReceipt.batch_id.label('batch_id'),
            literal(0.0).label('debit'),
            CustomerReceipt.amount_input.label('credit'),
            CustomerReceipt.amount_input.label('original_amount'),
            CustomerReceipt.currency.label('original_currency'),
            CustomerReceipt.exchange_rate.label('original_exchange_rate'),
            literal(0.0).label('debit_rwf'),
            func.coalesce(CustomerReceipt.amount_rwf, 0).label('credit_rwf'),
            CustomerReceipt.proof_image_path.label('proof_path'),
            CustomerReceipt.receipt_type.label('detail'),
        )
        .where(
            CustomerReceipt.customer == customer_name,
            CustomerReceipt.mineral_type.in_(aliases),
        )
    )
    if from_dt:
        receipts_stmt = receipts_stmt.where(CustomerReceipt.received_at >= from_dt)
    if to_dt:
        receipts_stmt = receipts_stmt.where(CustomerReceipt.received_at <= to_dt)

    # Include applied unearned allocations as credit entries so advances applied to batches show up
    allocations_stmt = (
        select(
            CustomerUnearnedAllocation.created_at.label('date'),
            literal(4).label('sort_key'),
            literal('allocation').label('entry_kind'),
            typed_int_null.label('plan_id'),
            CustomerUnearnedAllocation.id.label('receipt_id'),
            CustomerUnearnedAllocation.batch_id.label('batch_id'),
            literal(0.0).label('debit'),
            (CustomerUnearnedAllocation.applied_amount_rwf / CustomerUnearnedReceipt.exchange_rate).label('credit'),
            CustomerUnearnedReceipt.amount_input.label('original_amount'),
            CustomerUnearnedReceipt.currency.label('original_currency'),
            CustomerUnearnedReceipt.exchange_rate.label('original_exchange_rate'),
            literal(0.0).label('debit_rwf'),
            CustomerUnearnedAllocation.applied_amount_rwf.label('credit_rwf'),
            typed_text_null.label('proof_path'),
            literal('ADVANCE').label('detail'),
        )
        .select_from(CustomerUnearnedAllocation.__table__.join(CustomerUnearnedReceipt, CustomerUnearnedAllocation.unearned_id == CustomerUnearnedReceipt.id))
        .where(
            CustomerUnearnedReceipt.customer == customer_name,
            CustomerUnearnedAllocation.stock_mineral_type.in_(aliases),
        )
    )
    if from_dt:
        allocations_stmt = allocations_stmt.where(CustomerUnearnedAllocation.created_at >= from_dt)
    if to_dt:
        allocations_stmt = allocations_stmt.where(CustomerUnearnedAllocation.created_at <= to_dt)

    # Include unearned receipts (advances not yet allocated to any batch)
    unearned_stmt = (
        select(
            CustomerUnearnedReceipt.received_at.label('date'),
            literal(5).label('sort_key'),
            literal('unearned').label('entry_kind'),
            typed_int_null.label('plan_id'),
            CustomerUnearnedReceipt.id.label('receipt_id'),
            typed_text_null.label('batch_id'),
            literal(0.0).label('debit'),
            CustomerUnearnedReceipt.amount_input.label('credit'),
            CustomerUnearnedReceipt.amount_input.label('original_amount'),
            CustomerUnearnedReceipt.currency.label('original_currency'),
            CustomerUnearnedReceipt.exchange_rate.label('original_exchange_rate'),
            literal(0.0).label('debit_rwf'),
            func.coalesce(CustomerUnearnedReceipt.amount_rwf, 0).label('credit_rwf'),
            typed_text_null.label('proof_path'),
            literal('ADVANCE').label('detail'),
        )
        .where(
            CustomerUnearnedReceipt.customer == customer_name,
        )
    )
    if from_dt:
        unearned_stmt = unearned_stmt.where(CustomerUnearnedReceipt.received_at >= from_dt)
    if to_dt:
        unearned_stmt = unearned_stmt.where(CustomerUnearnedReceipt.received_at <= to_dt)

    if batch:
        plans_stmt = plans_stmt.where(BulkOutputPlan.batch_id == batch)
        deductions_stmt = deductions_stmt.where(BulkOutputPlan.batch_id == batch)
        receipts_stmt = receipts_stmt.where(CustomerReceipt.batch_id == batch)
        allocations_stmt = allocations_stmt.where(CustomerUnearnedAllocation.batch_id == batch)
        # Don't filter unearned by batch since they're not yet allocated

    # When viewing a specific batch, do NOT include unallocated unearned receipts
    # (they are not applied to the batch yet and would skew the batch remaining).
    if batch:
        ledger_union = union_all(plans_stmt, deductions_stmt, receipts_stmt, allocations_stmt).subquery('ledger_union')
    else:
        ledger_union = union_all(plans_stmt, deductions_stmt, receipts_stmt, allocations_stmt, unearned_stmt).subquery('ledger_union')

    # Count total rows for pagination
    total_q = db.session.query(func.count()).select_from(ledger_union)
    total_rows = int(total_q.scalar() or 0)

    # Get base currency from the first plan (agreement) if it exists
    base_currency = 'RWF'
    base_exchange_rate = 1.0
    plan = db.session.query(BulkOutputPlan).filter(BulkOutputPlan.customer == customer_name, BulkOutputPlan.mineral_type.in_(aliases)).first()
    if plan:
        base_currency = plan.currency or 'RWF'
        base_exchange_rate = plan.exchange_rate or 1.0

    def _base_amount(amount_input: float | None, amount_rwf: float | None, row_currency: str | None, row_rate: float | None) -> float:
        return float(_to_base_currency(amount_input, amount_rwf, row_currency, row_rate, base_currency, base_exchange_rate) or 0.0)

    # Aggregate totals (unpaginated): sum of debits and credits in base currency
    totals_q = db.session.query(
        func.coalesce(func.sum(ledger_union.c.debit), 0).label('total_debit'),
        func.coalesce(func.sum(ledger_union.c.credit), 0).label('total_credit')
    ).select_from(ledger_union).one()
    total_expected = float(totals_q.total_debit or 0.0)
    total_credits = float(totals_q.total_credit or 0.0)
    remaining = float(max(total_expected - total_credits, 0.0))

    summary_q = db.session.query(
        func.coalesce(func.sum(case((ledger_union.c.entry_kind == 'deduction', ledger_union.c.credit), else_=0)), 0).label('total_deductions'),
        func.coalesce(func.sum(case((ledger_union.c.entry_kind == 'receipt', ledger_union.c.credit), else_=0)), 0).label('total_settlements'),
        func.coalesce(func.sum(case((ledger_union.c.entry_kind == 'allocation', ledger_union.c.credit), else_=0)), 0).label('total_advances'),
    ).select_from(ledger_union).one()
    total_deductions = float(summary_q.total_deductions or 0.0)
    total_settlements = float(summary_q.total_settlements or 0.0)
    total_advances = float(summary_q.total_advances or 0.0)

    # Pagination handling: if page/per_page provided, apply limit/offset
    ledger_rows = []
    start_balance = 0.0
    if page and per_page:
        if page < 1:
            page = 1
        offset_val = (page - 1) * per_page

        # find cutoff row (first row on this page) to compute running balance before page
        cutoff_row = db.session.query(ledger_union).order_by(ledger_union.c.date.asc(), ledger_union.c.sort_key.asc()).offset(offset_val).limit(1).first()
        if cutoff_row:
            cutoff_date = cutoff_row.date
            cutoff_sort = cutoff_row.sort_key

            # sum all debits - credits strictly before cutoff
            before_q = db.session.query(
                func.coalesce(func.sum(ledger_union.c.debit), 0).label('d'),
                func.coalesce(func.sum(ledger_union.c.credit), 0).label('c')
            ).filter(
                or_(
                    ledger_union.c.date < cutoff_date,
                    and_(ledger_union.c.date == cutoff_date, ledger_union.c.sort_key < cutoff_sort)
                )
            )
            before_totals = before_q.one()
            start_balance = float((before_totals.d or 0) - (before_totals.c or 0))

        page_rows = db.session.query(ledger_union).order_by(ledger_union.c.date.asc(), ledger_union.c.sort_key.asc()).limit(per_page).offset(offset_val).all()
        ledger_rows = [
            dict(
                date=r._mapping['date'],
                sort_key=r._mapping['sort_key'],
                entry_kind=r._mapping.get('entry_kind'),
                plan_id=r._mapping.get('plan_id'),
                receipt_id=r._mapping.get('receipt_id'),
                batch_id=r._mapping['batch_id'],
                debit=float(r._mapping['debit'] or 0.0),
                credit=float(r._mapping['credit'] or 0.0),
                debit_rwf=float(r._mapping.get('debit_rwf') or 0.0),
                credit_rwf=float(r._mapping.get('credit_rwf') or 0.0),
                proof_path=r._mapping.get('proof_path'),
                detail=r._mapping.get('detail'),
                original_amount=float(r._mapping.get('original_amount') or 0.0),
                original_currency=r._mapping.get('original_currency'),
                original_exchange_rate=float(r._mapping.get('original_exchange_rate') or 1.0),
            )
            for r in page_rows
        ]
    else:
        # no pagination requested — return full ledger
        all_rows = db.session.query(ledger_union).order_by(ledger_union.c.date.asc(), ledger_union.c.sort_key.asc()).all()
        ledger_rows = [
            dict(
                date=r._mapping['date'],
                sort_key=r._mapping['sort_key'],
                entry_kind=r._mapping.get('entry_kind'),
                plan_id=r._mapping.get('plan_id'),
                receipt_id=r._mapping.get('receipt_id'),
                batch_id=r._mapping['batch_id'],
                debit=float(r._mapping['debit'] or 0.0),
                credit=float(r._mapping['credit'] or 0.0),
                debit_rwf=float(r._mapping.get('debit_rwf') or 0.0),
                credit_rwf=float(r._mapping.get('credit_rwf') or 0.0),
                proof_path=r._mapping.get('proof_path'),
                detail=r._mapping.get('detail'),
                original_amount=float(r._mapping.get('original_amount') or 0.0),
                original_currency=r._mapping.get('original_currency'),
                original_exchange_rate=float(r._mapping.get('original_exchange_rate') or 1.0),
            )
            for r in all_rows
        ]

    # Attach plan summaries for batches visible on this page
    visible_batches = {r['batch_id'] for r in ledger_rows if r.get('batch_id')}
    plan_map = {}
    if visible_batches:
        plans = BulkOutputPlan.query.filter(BulkOutputPlan.batch_id.in_(visible_batches)).all()
        for p in plans:
            plan_map[p.batch_id] = _compute_plan_averages(p)

    # Build final ledger with running balance starting from start_balance
    ledger = []
    running = float(start_balance)
    running_rwf = float(start_balance * base_exchange_rate)
    for r in ledger_rows:
        summary = plan_map.get(r.get('batch_id')) or {}
        entry_kind = (r.get('entry_kind') or '').strip().lower()
        detail = (r.get('detail') or '').strip()
        debit_base = _base_amount(r.get('debit'), r.get('debit_rwf'), r.get('original_currency'), r.get('original_exchange_rate'))
        credit_base = _base_amount(r.get('credit'), r.get('credit_rwf'), r.get('original_currency'), r.get('original_exchange_rate'))
        if entry_kind == 'deduction':
            description = f"Deduction / Expense: {detail or 'Adjustment'}"
        elif entry_kind == 'allocation':
            description = f"Advance Applied: {detail or 'Advance'}"
        elif entry_kind == 'receipt':
            description = f"Customer Settlement: {detail or 'Payment'}"
        elif entry_kind == 'unearned':
            description = "ADVANCE (Pending Allocation)"
        else:
            description = 'Agreement'

        ledger.append({
            'date': r.get('date'),
            'description': description,
            'debit': debit_base,
            'credit': credit_base,
            'debit_rwf': float(r.get('debit_rwf', 0) or 0.0),
            'credit_rwf': float(r.get('credit_rwf', 0) or 0.0),
            'original_amount': float(r.get('original_amount') or 0.0),
            'original_currency': r.get('original_currency'),
            'original_exchange_rate': float(r.get('original_exchange_rate') or 1.0),
            'entry_kind': r.get('entry_kind'),
            'plan_id': r.get('plan_id'),
            'receipt_id': r.get('receipt_id'),
            'mineral_type': mineral,
            'batch_id': r.get('batch_id'),
            'qty': float(summary.get('total_qty') or 0.0),
            'moyenne': float(summary.get('moyenne') or 0.0),
            'moyenne_nb': float(summary.get('moyenne_nb') or 0.0),
            'proof_path': r.get('proof_path'),
            'detail': detail,
            'balance': 0.0,
            'balance_rwf': 0.0,
        })
        running += debit_base
        running -= credit_base
        running_rwf += float(r.get('debit_rwf', 0) or 0.0)
        running_rwf -= float(r.get('credit_rwf', 0) or 0.0)
        ledger[-1]['balance'] = running
        ledger[-1]['balance_rwf'] = running_rwf
        ledger[-1]['base_currency'] = base_currency

    if ledger:
        remaining = float(max(ledger[-1]['balance'], 0.0))

    return ledger, total_expected, total_deductions, total_settlements, total_advances, remaining


@core_bp.route('/receipts/copper/customer_ledger')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def copper_customer_ledger_index():
    return redirect(url_for('core.consolidated_customer_ledger_index'))


@core_bp.route('/receipts/customer_ledger')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def customer_ledger_index():
    return redirect(url_for('core.consolidated_customer_ledger_index'))


@core_bp.route('/receipts/customers')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def consolidated_customer_ledger_index():
    try:
        page = int(request.args.get('page') or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)
    try:
        per_page = int(request.args.get('per_page') or 20)
    except (TypeError, ValueError):
        per_page = 20
    per_page = min(max(per_page, 1), 100)

    customer_union = union_all(
        select(BulkOutputPlan.customer.label('customer')).where(
            BulkOutputPlan.customer.isnot(None),
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
        ),
        select(CustomerReceipt.customer.label('customer')).where(
            CustomerReceipt.customer.isnot(None),
        ),
        select(CustomerUnearnedReceipt.customer.label('customer')).where(
            CustomerUnearnedReceipt.customer.isnot(None),
        ),
    ).subquery()

    customers_query = (
        db.session.query(
            customer_union.c.customer.label('customer'),
            func.lower(customer_union.c.customer).label('customer_sort'),
        )
        .filter(customer_union.c.customer.isnot(None))
        .filter(func.trim(customer_union.c.customer) != '')
        .group_by(customer_union.c.customer, func.lower(customer_union.c.customer))
        .order_by(func.lower(customer_union.c.customer).asc(), customer_union.c.customer.asc())
    )
    customers_pagination = customers_query.paginate(page=page, per_page=per_page, error_out=False)
    customer_names = [name for name, _sort in customers_pagination.items if name]

    return render_template(
        'negotiator/customer_ledger_index.html',
        customers=customer_names,
        customers_pagination=customers_pagination,
        mineral_type='all',
        page_size=per_page,
    )


@core_bp.route('/receipts/customers/<customer>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def consolidated_customer_ledger(customer: str):
    copper_batches = _customer_batch_cards('copper', customer)
    cass_batches = _customer_batch_cards('cassiterite', customer)
    batches = []
    for b in (copper_batches or []):
        row = dict(b)
        row['mineral_type'] = 'copper'
        batches.append(row)
    for b in (cass_batches or []):
        row = dict(b)
        row['mineral_type'] = 'cassiterite'
        batches.append(row)

    def _sort_key(item: dict):
        return (
            -float(item.get('expected') or 0.0),
            str(item.get('mineral_type') or ''),
            str(item.get('batch_id') or ''),
        )

    batches.sort(key=_sort_key)
    user_role = getattr(current_user, 'role', None)
    return render_template(
        'negotiator/customer_ledger_batches.html',
        customer=customer,
        batches=batches,
        mineral_type='all',
        user_role=user_role,
        is_readonly=(user_role == 'boss')
    )


@core_bp.route('/receipts/customers/<customer>/<mineral_type>/<batch_id>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def consolidated_customer_ledger_batch(customer: str, mineral_type: str, batch_id: str):
    preset, filter_from, filter_to = _customer_ledger_filter_context()
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    per_page = int(request.args.get('per_page', 50))
    ledger, total_owed, total_deductions, total_settlements, total_advances, remaining = _customer_ledger_data(mineral_type, customer, batch_id=batch_id, page=page, per_page=per_page, filter_from=filter_from, filter_to=filter_to)
    user_role = getattr(current_user, 'role', None)
    return render_template(
        'negotiator/customer_ledger.html',
        customer=customer,
        batch_id=batch_id,
        ledger=ledger,
        total_owed=total_owed,
        total_deductions=total_deductions,
        total_settlements=total_settlements,
        total_advances=total_advances,
        remaining=remaining,
        mineral_type=mineral_type,
        user_role=user_role,
        is_readonly=(user_role == 'boss'),
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
    )


@core_bp.route('/receipts/cassiterite/customer_ledger')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def cassiterite_customer_ledger_index():
    return redirect(url_for('core.consolidated_customer_ledger_index'))


@core_bp.route('/receipts/copper/customer_ledger/<customer>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def copper_customer_ledger(customer: str):
    """Display list of copper batches for a customer.
    
    Data Source: Single source of truth
    - BulkOutputPlan (agreements) + CustomerReceipt (payments)
    
    Access Control:
    - negotiator: Full access (can record payments)
    - accountant: Full access (can record payments)
    - boss: Read-only (can view ledgers only)
    - admin: Full access
    
    Outstanding Calculation: total_expected_amount - sum(receipts)
    """
    batches = _customer_batch_cards('copper', customer)
    user_role = getattr(current_user, 'role', None)
    return render_template(
        'negotiator/customer_ledger_batches.html',
        customer=customer,
        batches=batches,
        mineral_type='copper',
        user_role=user_role,
        is_readonly=(user_role == 'boss')
    )


@core_bp.route('/receipts/copper/customer_ledger/<customer>/<batch_id>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def copper_customer_ledger_batch(customer: str, batch_id: str):
    """Display full ledger (debit/credit) for a copper customer batch.
    
    Data Source: Single source of truth
    - Debit entries: BulkOutputPlan rows (what customer agreed to pay)
    - Credit entries: CustomerReceipt rows (payments received)
    - Outstanding: total_expected - sum(receipts)
    
    Ledger Structure:
    [
        {date, description, debit, credit, balance, batch_id, qty, moyenne, moyenne_nb},
        ...
    ]
    
    Access Control:
    - negotiator: Can view and has action buttons (Record Receipts, Update Debts)
    - accountant: Can view and has action buttons
    - boss: Can view ONLY (read-only, no action buttons)
    - admin: Can view and has action buttons
    """
    # support DB-level pagination for ledger entries
    preset, filter_from, filter_to = _customer_ledger_filter_context()
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    per_page = int(request.args.get('per_page', 50))
    ledger, total_owed, total_deductions, total_settlements, total_advances, remaining = _customer_ledger_data('copper', customer, batch_id=batch_id, page=page, per_page=per_page, filter_from=filter_from, filter_to=filter_to)
    user_role = getattr(current_user, 'role', None)
    
    return render_template(
        'negotiator/customer_ledger.html',
        customer=customer,
        batch_id=batch_id,
        ledger=ledger,
        total_owed=total_owed,
        total_deductions=total_deductions,
        total_settlements=total_settlements,
        total_advances=total_advances,
        remaining=remaining,
        mineral_type='copper',
        user_role=user_role,
        is_readonly=(user_role == 'boss'),
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
    )


@core_bp.route('/receipts/cassiterite/customer_ledger/<customer>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def cassiterite_customer_ledger(customer: str):
    """Display list of cassiterite batches for a customer.
    
    Data Source: Single source of truth
    - BulkOutputPlan (agreements) + CustomerReceipt (payments)
    
    Access Control:
    - negotiator: Full access (can record payments)
    - accountant: Full access (can record payments)
    - boss: Read-only (can view ledgers only)
    - admin: Full access
    
    Outstanding Calculation: total_expected_amount - sum(receipts)
    """
    batches = _customer_batch_cards('cassiterite', customer)
    user_role = getattr(current_user, 'role', None)
    return render_template(
        'negotiator/customer_ledger_batches.html',
        customer=customer,
        batches=batches,
        mineral_type='cassiterite',
        user_role=user_role,
        is_readonly=(user_role == 'boss')
    )


@core_bp.route('/receipts/cassiterite/customer_ledger/<customer>/<batch_id>')
@role_required('negotiator', 'accountant', 'boss', 'admin')
def cassiterite_customer_ledger_batch(customer: str, batch_id: str):
    """Display full ledger (debit/credit) for a cassiterite customer batch.
    
    Data Source: Single source of truth
    - Debit entries: BulkOutputPlan rows (what customer agreed to pay)
    - Credit entries: CustomerReceipt rows (payments received)
    - Outstanding: total_expected - sum(receipts)
    
    Ledger Structure:
    [
        {date, description, debit, credit, balance, batch_id, qty, moyenne, moyenne_nb},
        ...
    ]
    
    Access Control:
    - negotiator: Can view and has action buttons (Record Receipts, Update Debts)
    - accountant: Can view and has action buttons
    - boss: Can view ONLY (read-only, no action buttons)
    - admin: Can view and has action buttons
    """
    preset, filter_from, filter_to = _customer_ledger_filter_context()
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    per_page = int(request.args.get('per_page', 50))
    ledger, total_owed, total_deductions, total_settlements, total_advances, remaining = _customer_ledger_data('cassiterite', customer, batch_id=batch_id, page=page, per_page=per_page, filter_from=filter_from, filter_to=filter_to)
    user_role = getattr(current_user, 'role', None)
    
    return render_template(
        'negotiator/customer_ledger.html',
        customer=customer,
        batch_id=batch_id,
        ledger=ledger,
        total_owed=total_owed,
        total_deductions=total_deductions,
        total_settlements=total_settlements,
        total_advances=total_advances,
        remaining=remaining,
        mineral_type='cassiterite',
        user_role=user_role,
        is_readonly=(user_role == 'boss'),
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
    )


def _batch_debt_options():
    """Return customer/batch outstanding options for debt update page.
    
    Single source of truth: Use ONLY BulkOutputPlan + CustomerReceipt (all in RWF).
    Outstanding = plan.total_expected_amount - sum(receipts.amount_rwf) - sum(allocations) - sum(deductions)
    
    NEGOTIATOR WORKFLOW: Only copper/cassiterite mineral type (not coltan).
    """
    # Query all active agreements for COPPER/CASSITERITE ONLY.
    plans = (
        BulkOutputPlan.query
        .filter(
            BulkOutputPlan.customer.isnot(None),
            BulkOutputPlan.batch_id.isnot(None),
            BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
            BulkOutputPlan.mineral_type.in_(_mineral_aliases('copper') + _mineral_aliases('cassiterite')),
        )
        .all()
    )

    receipts = (
        CustomerReceipt.query
        .filter(CustomerReceipt.customer.isnot(None), CustomerReceipt.batch_id.isnot(None))
        .all()
    )

    allocations = (
        CustomerUnearnedAllocation.query
        .filter(CustomerUnearnedAllocation.batch_id.isnot(None))
        .all()
    )

    deductions = (
        BatchDeduction.query
        .all()
    )

    rows_map: dict[tuple[str, str, str], dict] = {}

    for p in plans:
        canonical_mineral = _canonical_mineral_type(p.mineral_type)
        if not canonical_mineral:
            continue

        aliases = _mineral_aliases(p.mineral_type)
        base_currency = (getattr(p, 'currency', None) or 'RWF').upper()
        base_rate = float(getattr(p, 'exchange_rate', 1.0) or 1.0)

        # Sum all RWF amounts directly (no currency conversion)
        total_paid = float(sum(r.amount_rwf or 0 for r in receipts if r.batch_id == p.batch_id and r.mineral_type in aliases))
        total_allocated = float(sum(a.applied_amount_rwf or 0 for a in allocations if a.batch_id == p.batch_id))
        total_deducted = float(sum(d.amount_rwf or 0 for d in deductions if d.batch_id == p.batch_id))

        planned_total = float(p.total_expected_amount or 0.0)
        remaining_rwf = float(max(planned_total - total_paid - total_allocated - total_deducted, 0.0))
        
        # For display: convert RWF to USD if needed
        remaining_display = remaining_rwf
        if base_currency == 'USD' and base_rate > 0:
            remaining_display = remaining_rwf / base_rate

        # Keep the customer visible in the dropdown even when the remaining
        # balance is already zero. Users still need to find historical customers
        # they have searched or recorded receipts for.
        row_visible = True

        key = (canonical_mineral, p.customer, p.batch_id)
        rows_map[key] = {
            'mineral_type': canonical_mineral,
            'customer': p.customer,
            'batch_id': p.batch_id,
            'bulk_plan_id': int(p.id),
            'remaining': remaining_display,
            'remaining_rwf': remaining_rwf,
            'currency': base_currency,
            'exchange_rate': base_rate,
            'visible': row_visible,
        }

    rows = [r for r in rows_map.values() if r.get('visible')]

    # Attach batch quality metadata
    plan_map = {}
    for p in plans:
        canonical_mineral = _canonical_mineral_type(p.mineral_type)
        if canonical_mineral:
            plan_map[(canonical_mineral, p.batch_id)] = p

    for row in rows_map.values():
        p = plan_map.get((row['mineral_type'], row['batch_id']))
        if p:
            summary = _compute_plan_averages(p)
            row['moyenne'] = float(summary.get('moyenne') or 0.0)
            row['moyenne_nb'] = float(summary.get('moyenne_nb') or 0.0)
            row['qty'] = float(summary.get('total_qty') or 0.0)
        else:
            row['moyenne'] = 0.0
            row['moyenne_nb'] = 0.0
            row['qty'] = 0.0

    rows.sort(key=lambda x: (x['customer'].lower(), x['mineral_type'], x['batch_id']))
    customer_info: dict[str, dict] = {}
    for row in rows:
        customer = row['customer']
        currency = row['currency']
        info = customer_info.setdefault(customer, {'balances': {}, 'labels': []})
        info['balances'][currency] = float(info['balances'].get(currency, 0.0) + float(row['remaining'] or 0.0))
        if currency == 'USD':
            info['labels'].append(f"{float(row['remaining'] or 0.0):,.2f} USD (~{float(row.get('remaining_rwf') or 0.0):,.2f} RWF)")
        else:
            info['labels'].append(f"{float(row['remaining'] or 0.0):,.2f} RWF")

    customers = []
    for name, info in sorted(customer_info.items(), key=lambda i: i[0].lower()):
        balances = info['balances']
        balance_parts = []
        for currency, amount in sorted(balances.items()):
            if currency == 'USD':
                balance_parts.append(f"{amount:,.2f} USD")
            else:
                balance_parts.append(f"{amount:,.2f} {currency}")
        customers.append({
            'name': name,
            'remaining': float(sum(balances.values())),
            'currency': ', '.join(sorted(balances.keys())) if balances else 'RWF',
            'balance_label': ' | '.join(balance_parts) if balance_parts else '0.00 RWF',
        })
    return customers, rows



@core_bp.route('/receipts/update_debts', methods=['GET', 'POST'])
@role_required('negotiator', 'admin')
def update_debts():
    can_record = getattr(current_user, 'role', None) in {'negotiator', 'admin'}
    customers, batch_options = _batch_debt_options()
    option_map = {(b['customer'], b['mineral_type'], b['batch_id']): b for b in batch_options}

    if request.method == 'POST':
        if not can_record:
            _flash_and_notify('Only negotiator can record customer payments.', 'warning')
            return redirect(url_for('core.update_debts'))

        customer = (request.form.get('customer') or '').strip()
        customer = ' '.join(customer.split())
        mineral_type = _canonical_mineral_type(request.form.get('mineral_type'))
        batch_id = (request.form.get('batch_id') or '').strip()
        if not customer:
            _flash_and_notify('Customer is required.', 'danger')
            return redirect(url_for('core.update_debts'))
        if mineral_type not in {'copper', 'cassiterite'}:
            _flash_and_notify('Mineral is required.', 'danger')
            return redirect(url_for('core.update_debts'))
        if not batch_id:
            _flash_and_notify('Batch is required.', 'danger')
            return redirect(url_for('core.update_debts'))

        try:
            amount_input = float(request.form.get('amount_input') or 0)
        except ValueError:
            _flash_and_notify('Amount must be a number.', 'danger')
            return redirect(url_for('core.update_debts'))

        currency = (request.form.get('currency') or 'RWF').upper()
        exchange_rate_input = request.form.get('exchange_rate')
        receipt_type = (request.form.get('receipt_type') or CustomerReceiptType.INSTALLMENT.value).upper()
        payment_channel = (request.form.get('payment_channel') or CustomerReceiptChannel.CASH.value).upper()
        note = request.form.get('note') or ''

        try:
            amount_rwf, exchange_rate = _normalize_amount_to_rwf(amount_input, currency, exchange_rate_input)
        except ValueError as exc:
            _flash_and_notify(str(exc), 'danger')
            return redirect(url_for('core.update_debts'))

        if amount_rwf <= 0:
            _flash_and_notify('Amount must be greater than zero.', 'danger')
            return redirect(url_for('core.update_debts'))

        selected = option_map.get((customer, mineral_type, batch_id))
        if not selected:
            _flash_and_notify('Selected customer batch was not found.', 'warning')
            return redirect(url_for('core.update_debts'))

        outstanding = float(selected.get('remaining') or 0.0)
        outstanding_rwf = float(selected.get('remaining_rwf') or 0.0)
        if outstanding_rwf <= 0 and receipt_type != CustomerReceiptType.ADVANCE.value:
            _flash_and_notify('This customer batch has no outstanding balance. Use advance-only if you are recording money before final agreement.', 'warning')
            return redirect(url_for('core.update_debts'))
        if receipt_type == CustomerReceiptType.ADVANCE.value:
            _flash_and_notify('Advance receipts must be recorded from Record Customer Receipts or Unearned Receipts. Update Debts is for settlements only.', 'warning')
            return redirect(url_for('core.update_debts'))
        if outstanding_rwf > 0 and amount_rwf - outstanding_rwf > 0.01:
            _flash_and_notify('Payment exceeds outstanding amount for selected batch.', 'danger')
            return redirect(url_for('core.update_debts'))

        stage = 'ADVANCE'
        existing_count = CustomerReceipt.query.filter_by(customer=customer, mineral_type=mineral_type, batch_id=batch_id).count()
        if existing_count > 0:
            stage = 'INSTALLMENT'

        applied_total = _apply_receipt_to_batch(mineral_type, batch_id, amount_rwf, stage)
        if applied_total <= 0:
            _flash_and_notify('Could not apply payment to selected batch outputs.', 'danger')
            return redirect(url_for('core.update_debts'))

        remaining_after = _batch_outstanding_rwf(mineral_type, batch_id)
        final_receipt_type = receipt_type
        if remaining_after <= 0.01:
            final_receipt_type = CustomerReceiptType.FINAL_SETTLEMENT.value

        receipt = CustomerReceipt(
            mineral_type=mineral_type,
            batch_id=batch_id,
            customer=customer,
            bulk_plan_id=selected.get('bulk_plan_id'),
            received_at=datetime.utcnow(),
            receipt_type=final_receipt_type,
            payment_channel=payment_channel,
            amount_input=amount_input,
            currency=currency,
            exchange_rate=exchange_rate,
            amount_rwf=float(applied_total),
            created_by_id=getattr(current_user, 'id', None),
            note=note,
            proof_image_path=None,
            proof_uploaded_at=None,
        )
        db.session.add(receipt)
        db.session.flush()

        # Notify bosses so receipt inflows are always visible.
        try:
            boss_rows = db.session.query(User.id).filter_by(role='boss', is_active=True).all()
            for (boss_id,) in boss_rows:
                create_notification(
                    user_id=int(boss_id),
                    type_='CUSTOMER_RECEIPT_RECORDED',
                    message=(
                        f"Negotiator {getattr(current_user, 'username', 'unknown')} yakiriye amafaranga y’umukiriya {customer} "
                        f"(Batch {batch_id}, {mineral_type}). Amount: {float(applied_total or 0):,.2f} RWF."
                    ),
                    related_type='customer_receipt',
                    related_id=int(receipt.id),
                )
        except Exception:
            logger.exception('update_debts: failed to notify bosses')

        db.session.commit()
        if mineral_type == 'cassiterite':
            return redirect(url_for('core.cassiterite_customer_ledger_batch', customer=customer, batch_id=batch_id))
        return redirect(url_for('core.copper_customer_ledger_batch', customer=customer, batch_id=batch_id))

    # Also fetch persisted notifications so the page can render a dedicated
    # message area (flash messages are persisted via _flash_and_notify).
    notifications = []
    unread_count = 0
    if getattr(current_user, 'is_authenticated', False):
        try:
            notifications, unread_count = fetch_user_notifications(current_user.id)
        except Exception:
            logger.exception('update_debts: failed to fetch notifications')

    try:
        handover_receipts = (
            CustomerReceipt.query
            .filter(
                CustomerReceipt.payment_channel == CustomerReceiptChannel.CASH.value,
                CustomerReceipt.is_collected == False,
                CustomerReceipt.is_handed_over == False,
            )
            .order_by(CustomerReceipt.received_at.desc(), CustomerReceipt.id.desc())
            .limit(120)
            .all()
        )
    except Exception:
        handover_receipts = []

    return render_template(
        'negotiator/update_debts.html',
        customers=customers,
        batch_options=batch_options,
        can_record=can_record,
        notifications=notifications,
        unread_count=unread_count,
        handover_receipts=handover_receipts,
    )


@core_bp.route("/boss/copper/customer_ledger/<customer>")
@role_required("boss", "accountant", "admin")
def boss_copper_customer_ledger(customer: str):
    """Boss read-only view of a copper customer ledger.
    
    Data Source: Single source of truth
    - BulkOutputPlan (agreements) + CustomerReceipt (payments)
    
    Reuses the unified _customer_ledger_data() so boss view stays in sync
    with negotiator/accountant ledgers (accounts for agreements and receipts).
    
    Boss CANNOT:
    - Record customer payments
    - Update debts
    - Modify agreements
    
    Boss CAN:
    - View complete ledger history
    - See all agreements and payments
    - Monitor customer balances
    """
    preset, filter_from, filter_to = _customer_ledger_filter_context()
    ledger, total_expected, total_deductions, total_settlements, total_advances, remaining = _customer_ledger_data('copper', customer, filter_from=filter_from, filter_to=filter_to)
    user_role = getattr(current_user, 'role', None)
    
    return render_template(
        "negotiator/customer_ledger.html",
        customer=customer,
        batch_id=None,
        ledger=ledger,
        total_owed=total_expected,
        total_deductions=total_deductions,
        total_settlements=total_settlements,
        total_advances=total_advances,
        remaining=remaining,
        mineral_type='copper',
        user_role=user_role,
        is_readonly=True,
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
    )


@core_bp.route("/boss/cassiterite/customer_ledger/<customer>")
@role_required("boss", "accountant", "admin")
def boss_cassiterite_customer_ledger(customer: str):
    """Boss read-only view of a cassiterite customer ledger.
    
    Data Source: Single source of truth
    - BulkOutputPlan (agreements) + CustomerReceipt (payments)
    
    Reuses the unified _customer_ledger_data() so boss view stays in sync
    with negotiator/accountant ledgers (accounts for agreements and receipts).
    
    Boss CANNOT:
    - Record customer payments
    - Update debts
    - Modify agreements
    
    Boss CAN:
    - View complete ledger history
    - See all agreements and payments
    - Monitor customer balances
    """
    preset, filter_from, filter_to = _customer_ledger_filter_context()
    ledger, total_expected, total_deductions, total_settlements, total_advances, remaining = _customer_ledger_data('cassiterite', customer, filter_from=filter_from, filter_to=filter_to)
    user_role = getattr(current_user, 'role', None)
    
    return render_template(
        "negotiator/customer_ledger.html",
        customer=customer,
        batch_id=None,
        ledger=ledger,
        total_owed=total_expected,
        total_deductions=total_deductions,
        total_settlements=total_settlements,
        total_advances=total_advances,
        remaining=remaining,
        mineral_type='cassiterite',
        user_role=user_role,
        is_readonly=True,
        filter_preset=preset,
        filter_from=filter_from,
        filter_to=filter_to,
    )


@core_bp.route("/boss/copper/supplier_ledger/<supplier>")
@role_required("boss", "admin")
def boss_copper_supplier_ledger(supplier: str):
    return redirect(url_for("core.consolidated_supplier_ledger_lookup", supplier=supplier))


@core_bp.route("/boss/cassiterite/supplier_ledger/<supplier>")
@role_required("boss", "admin")
def boss_cassiterite_supplier_ledger(supplier: str):
    return redirect(url_for("core.consolidated_supplier_ledger_lookup", supplier=supplier))


@core_bp.route("/boss/copper/ledgers")
@role_required("boss", "accountant", "admin")
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


@core_bp.route("/boss/cassiterite/ledgers")
@role_required("boss", "accountant", "admin")
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

    # Remove owned notifications first so the required notification.user_id
    # column never has to be nulled out during the delete flush.
    db.session.query(Notification).filter(Notification.user_id == user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(request.referrer or url_for("core.admin_users"))


@core_bp.route("/admin/rebuild_aggregate/<mineral_type>", methods=["POST"])
@role_required("admin")
def admin_rebuild_aggregate(mineral_type: str):
    """Rebuild StockAggregate for a specific mineral type from current stock data.

    This fixes stale aggregate data that can occur when:
    - Delta updates are missed or fail
    - Direct database modifications bypass the application
    - Transaction rollbacks leave aggregate in inconsistent state
    - Historical data migration without aggregate updates
    """
    from sqlalchemy import func
    from core.models import StockAggregate

    if mineral_type not in ['cassiterite', 'copper']:
        flash(f"Invalid mineral type: {mineral_type}", "danger")
        return redirect(request.referrer or url_for("core.admin_users"))

    try:
        # Import the correct model based on mineral type
        if mineral_type == 'cassiterite':
            from cassiterite.models import CassiteriteStock as StockModel
        else:
            from copper.models import CopperStock as StockModel

        # Calculate current totals from actual stock data
        total_unit_percent = db.session.query(func.coalesce(func.sum(StockModel.unit_percent), 0)).filter(
            StockModel.local_balance > 0,
            StockModel.is_deleted.is_(False),
        ).scalar() or 0

        total_remaining_balance = db.session.query(func.coalesce(func.sum(StockModel.local_balance), 0)).filter(
            StockModel.local_balance > 0,
            StockModel.is_deleted.is_(False),
        ).scalar() or 0

        total_t_unity = db.session.query(func.coalesce(func.sum(StockModel.t_unity), 0)).filter(
            StockModel.local_balance > 0,
            StockModel.is_deleted.is_(False),
        ).scalar() or 0

        # Convert to float
        total_unit_percent = float(total_unit_percent or 0.0)
        total_remaining_balance = float(total_remaining_balance or 0.0)
        total_t_unity = float(total_t_unity or 0.0)

        # Calculate moyenne
        moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0

        # Update or create StockAggregate
        agg = db.session.query(StockAggregate).filter_by(mineral_type=mineral_type).with_for_update().first()
        if not agg:
            agg = StockAggregate(mineral_type=mineral_type)
            db.session.add(agg)

        old_qty = agg.total_quantity or 0
        old_wp = agg.total_weighted_percent or 0

        agg.total_quantity = total_remaining_balance
        agg.total_weighted_percent = total_unit_percent
        agg.total_t_unity = total_t_unity

        db.session.commit()

        flash(f"✓ {mineral_type.capitalize()} aggregate rebuilt successfully! "
              f"Old: {old_qty:.2f}kg → New: {total_remaining_balance:.2f}kg, "
              f"Moyenne: {moyenne * 100:.2f}%", "success")

    except Exception as e:
        db.session.rollback()
        logger.exception(f"admin_rebuild_aggregate failed for {mineral_type}")
        flash(f"✗ Error rebuilding {mineral_type} aggregate: {str(e)}", "danger")

    return redirect(request.referrer or url_for("core.admin_users"))
