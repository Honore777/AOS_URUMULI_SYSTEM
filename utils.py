# utils.py

import os
import time
import logging
import functools
from flask import current_app
import difflib
import re
from decimal import Decimal
from flask import jsonify

# Configure simple app-wide logging. Control level with the LOG_LEVEL env var.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def normalize_counterparty_name(name: str) -> str:
    raw = (name or '').strip().lower()
    if not raw:
        return ''
    # Replace punctuation and separators with spaces so "jean-paul" and "jean paul" normalize equally.
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return ' '.join(raw.split())


def generate_supplier_slug(name: str) -> str:
    """Generate a URL-safe slug from supplier name.
    
    Examples:
    - 'HARERIMANA ARSENE' -> 'harerimana-arsene'
    - '/COMIKA/SEZEKEYE' -> 'comika-sezekeye'
    - 'Jean-Paul O\'Brien' -> 'jean-paul-obrien'
    """
    if not name:
        return ''
    # Lowercase and process
    slug = (name or '').strip().lower()
    # Replace slashes and spaces with hyphens FIRST
    slug = re.sub(r'[\s/]+', '-', slug)
    # Remove other special chars (keep only alphanumeric and hyphens)
    slug = re.sub(r'[^a-z0-9\-]', '', slug)
    # Collapse multiple hyphens
    slug = re.sub(r'-+', '-', slug)
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    return slug


def close_name_matches(input_name: str, candidates: list[str], limit: int = 5, cutoff: float = 0.86) -> list[str]:
    needle = normalize_counterparty_name(input_name)
    if not needle:
        return []
    mapping = {}
    keys = []
    for c in candidates or []:
        key = normalize_counterparty_name(c)
        if not key:
            continue
        if key not in mapping:
            mapping[key] = c
            keys.append(key)
    if not keys:
        return []
    matches = difflib.get_close_matches(needle, keys, n=limit, cutoff=cutoff)
    return [mapping[m] for m in matches if m in mapping]


def to_decimal(value) -> Decimal:
    """Convert a numeric-like value to Decimal safely.

    - Strings and ints convert directly; floats are converted via str()
      to avoid binary float artifacts.
    - None returns Decimal('0').
    """
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    try:
        if isinstance(value, float):
            return Decimal(str(value))
        return Decimal(value)
    except Exception:
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal('0')


def to_number(value) -> float:
    """Convert Decimal/int/float-like to Python float for JSON/UI.

    Prefer using `Decimal` for internal accounting; convert to float only
    when preparing JSON or rendering templates.
    """
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except Exception:
        try:
            return float(str(value))
        except Exception:
            return 0.0


def _convert_decimals(obj):
    """Recursively convert Decimals in lists/dicts to floats for JSON."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_convert_decimals(v) for v in obj]
    return obj


def safe_jsonify(payload):
    """Flask-friendly jsonify that converts Decimal to float recursively.

    Use this in API endpoints that may return SQLAlchemy Numeric values.
    """
    try:
        return jsonify(_convert_decimals(payload))
    except Exception:
        # Fallback: attempt simple conversion via str()
        return jsonify(str(payload))


def calculate_consolidated_supplier_remaining_balance(supplier_name: str) -> float:
    """Return the supplier-wide remaining balance across copper and cassiterite.

    This is the shared source of truth for supplier-facing balance displays.
    """
    normalized = ' '.join((supplier_name or '').strip().lower().split())
    if not normalized:
        return 0.0

    try:
        from sqlalchemy import func, or_
        from config import db
        from copper.models import CopperStock, SupplierPayment as CopperSupplierPayment, CopperAdvanceAllocation
        from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment, CassiteriteAdvanceAllocation
        from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation, SupplierDeduction

        supplier_like = f"%{'%'.join(normalized.split())}%"

        copper_stock_debt = float(
            db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0))
            .filter(CopperStock.is_deleted.is_(False), func.lower(CopperStock.supplier).ilike(supplier_like))
            .scalar()
            or 0.0
        )
        cass_stock_debt = float(
            db.session.query(func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0))
            .filter(CassiteriteStock.is_deleted.is_(False), func.lower(CassiteriteStock.supplier).ilike(supplier_like))
            .scalar()
            or 0.0
        )

        copper_allocation = float(
            db.session.query(func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0))
            .join(CopperStock, CopperStock.id == CopperAdvanceAllocation.stock_id)
            .filter(CopperStock.is_deleted.is_(False), func.lower(CopperStock.supplier).ilike(supplier_like))
            .scalar()
            or 0.0
        )
        cass_allocation = float(
            db.session.query(func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0))
            .join(CassiteriteStock, CassiteriteStock.id == CassiteriteAdvanceAllocation.stock_id)
            .filter(CassiteriteStock.is_deleted.is_(False), func.lower(CassiteriteStock.supplier).ilike(supplier_like))
            .scalar()
            or 0.0
        )

        copper_paid = float(
            db.session.query(func.coalesce(func.sum(func.coalesce(CopperSupplierPayment.amount_rwf, CopperSupplierPayment.amount)), 0))
            .join(CopperStock, CopperStock.id == CopperSupplierPayment.stock_id, isouter=True)
            .filter(
                CopperSupplierPayment.is_deleted.is_(False),
                CopperSupplierPayment.is_advance.is_(False),
                or_(
                    func.lower(CopperStock.supplier).ilike(supplier_like),
                    func.lower(func.coalesce(CopperSupplierPayment.supplier_name, '')).ilike(supplier_like),
                ),
            )
            .scalar()
            or 0.0
        )
        cass_paid = float(
            db.session.query(func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0))
            .join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id, isouter=True)
            .filter(
                CassiteriteSupplierPayment.is_deleted.is_(False),
                CassiteriteSupplierPayment.is_advance.is_(False),
                or_(
                    func.lower(CassiteriteStock.supplier).ilike(supplier_like),
                    func.lower(func.coalesce(CassiteriteSupplierPayment.supplier_name, '')).ilike(supplier_like),
                ),
            )
            .scalar()
            or 0.0
        )

        unified_advances = (
            db.session.query(UnifiedSupplierAdvance.amount_rwf)
            .filter(
                UnifiedSupplierAdvance.is_deleted.is_(False),
                UnifiedSupplierAdvance.supplier_name_norm == normalized,
            )
            .all()
        )
        advance_credit = float(sum(float(a[0] or 0.0) for a in unified_advances if float(a[0] or 0.0) > 0.0) or 0.0)
        refund_debit = float(sum(abs(float(a[0] or 0.0)) for a in unified_advances if float(a[0] or 0.0) < 0.0) or 0.0)

        allocation_total = copper_allocation + cass_allocation
        stock_total = copper_stock_debt + cass_stock_debt
        paid_total = copper_paid + cass_paid

        supplier_deduction_credit = float(
            db.session.query(func.coalesce(func.sum(SupplierDeduction.amount_rwf), 0))
            .filter(SupplierDeduction.supplier_name.ilike(supplier_like))
            .scalar()
            or 0.0
        )
        remaining = stock_total + refund_debit - allocation_total - advance_credit - paid_total - supplier_deduction_credit
        return float(remaining or 0.0)
    except Exception:
        return 0.0


def calculate_consolidated_supplier_remaining_balances(supplier_names):
    """Return remaining balances for many suppliers in one batch.

    Keys in the returned mapping are normalized supplier names. The helper
    uses grouped queries for the common path and falls back to the single
    supplier helper for any names that do not match the grouped queries.
    """
    normalized_names = [
        ' '.join((name or '').strip().lower().split())
        for name in (supplier_names or [])
    ]
    normalized_names = [name for name in normalized_names if name]
    if not normalized_names:
        return {}

    unique_names = sorted(set(normalized_names))

    try:
        from config import db
        from copper.models import CopperStock, SupplierPayment as CopperSupplierPayment, CopperAdvanceAllocation
        from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment, CassiteriteAdvanceAllocation
        from core.models import UnifiedSupplierAdvance, UnifiedSupplierAdvanceAllocation, SupplierDeduction
        from sqlalchemy import func, or_

        copper_supplier_expr = func.lower(func.trim(CopperStock.supplier))
        cass_supplier_expr = func.lower(func.trim(CassiteriteStock.supplier))
        copper_payment_supplier_expr = func.lower(func.trim(func.coalesce(CopperStock.supplier, CopperSupplierPayment.supplier_name)))
        cass_payment_supplier_expr = func.lower(func.trim(func.coalesce(CassiteriteStock.supplier, CassiteriteSupplierPayment.supplier_name)))

        stock_totals = {}
        allocation_totals = {}
        paid_totals = {}
        refund_totals = {}
        deduction_totals = {}

        for supplier_norm, stock_debt in (
            db.session.query(
                copper_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(CopperStock.net_balance), 0).label('stock_debt'),
            )
            .filter(
                CopperStock.is_deleted.is_(False),
                copper_supplier_expr.in_(unique_names),
            )
            .group_by(copper_supplier_expr)
            .all()
        ):
            stock_totals[supplier_norm] = float(stock_totals.get(supplier_norm, 0.0) + float(stock_debt or 0.0))

        for supplier_norm, stock_debt in (
            db.session.query(
                cass_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(CassiteriteStock.balance_to_pay), 0).label('stock_debt'),
            )
            .filter(
                CassiteriteStock.is_deleted.is_(False),
                cass_supplier_expr.in_(unique_names),
            )
            .group_by(cass_supplier_expr)
            .all()
        ):
            stock_totals[supplier_norm] = float(stock_totals.get(supplier_norm, 0.0) + float(stock_debt or 0.0))

        for supplier_norm, allocation_total in (
            db.session.query(
                copper_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0).label('allocation_total'),
            )
            .join(CopperStock, CopperStock.id == CopperAdvanceAllocation.stock_id)
            .filter(
                CopperStock.is_deleted.is_(False),
                copper_supplier_expr.in_(unique_names),
            )
            .group_by(copper_supplier_expr)
            .all()
        ):
            allocation_totals[supplier_norm] = float(allocation_totals.get(supplier_norm, 0.0) + float(allocation_total or 0.0))

        for supplier_norm, allocation_total in (
            db.session.query(
                cass_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0).label('allocation_total'),
            )
            .join(CassiteriteStock, CassiteriteStock.id == CassiteriteAdvanceAllocation.stock_id)
            .filter(
                CassiteriteStock.is_deleted.is_(False),
                cass_supplier_expr.in_(unique_names),
            )
            .group_by(cass_supplier_expr)
            .all()
        ):
            allocation_totals[supplier_norm] = float(allocation_totals.get(supplier_norm, 0.0) + float(allocation_total or 0.0))

        for supplier_norm, paid_total in (
            db.session.query(
                copper_payment_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(func.coalesce(CopperSupplierPayment.amount_rwf, CopperSupplierPayment.amount)), 0).label('paid_total'),
            )
            .join(CopperStock, CopperStock.id == CopperSupplierPayment.stock_id, isouter=True)
            .filter(
                CopperSupplierPayment.is_deleted.is_(False),
                CopperSupplierPayment.is_advance.is_(False),
                copper_payment_supplier_expr.in_(unique_names),
            )
            .group_by(copper_payment_supplier_expr)
            .all()
        ):
            paid_totals[supplier_norm] = float(paid_totals.get(supplier_norm, 0.0) + float(paid_total or 0.0))

        for supplier_norm, paid_total in (
            db.session.query(
                cass_payment_supplier_expr.label('supplier_norm'),
                func.coalesce(func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)), 0).label('paid_total'),
            )
            .join(CassiteriteStock, CassiteriteStock.id == CassiteriteSupplierPayment.stock_id, isouter=True)
            .filter(
                CassiteriteSupplierPayment.is_deleted.is_(False),
                CassiteriteSupplierPayment.is_advance.is_(False),
                cass_payment_supplier_expr.in_(unique_names),
            )
            .group_by(cass_payment_supplier_expr)
            .all()
        ):
            paid_totals[supplier_norm] = float(paid_totals.get(supplier_norm, 0.0) + float(paid_total or 0.0))

        for supplier_norm, deduction_total in (
            db.session.query(
                func.lower(func.trim(SupplierDeduction.supplier_name)).label('supplier_norm'),
                func.coalesce(func.sum(SupplierDeduction.amount_rwf), 0).label('deduction_total'),
            )
            .group_by(func.lower(func.trim(SupplierDeduction.supplier_name)))
            .all()
        ):
            deduction_totals[supplier_norm] = float(deduction_totals.get(supplier_norm, 0.0) + float(deduction_total or 0.0))

        unified_rows = (
            db.session.query(
                UnifiedSupplierAdvance.supplier_name_norm,
                UnifiedSupplierAdvance.amount_rwf,
            )
            .filter(
                UnifiedSupplierAdvance.is_deleted.is_(False),
                UnifiedSupplierAdvance.supplier_name_norm.in_(unique_names),
            )
            .all()
        )
        for supplier_norm, amount_rwf in unified_rows:
            value = float(amount_rwf or 0.0)
            if value > 0:
                paid_totals[supplier_norm] = float(paid_totals.get(supplier_norm, 0.0) + value)
            elif value < 0:
                refund_totals[supplier_norm] = float(refund_totals.get(supplier_norm, 0.0) + abs(value))

        remaining_map = {}
        for supplier_norm in unique_names:
            stock_total = float(stock_totals.get(supplier_norm, 0.0))
            allocation_total = float(allocation_totals.get(supplier_norm, 0.0))
            paid_total = float(paid_totals.get(supplier_norm, 0.0))
            refund_debit = float(refund_totals.get(supplier_norm, 0.0))
            deduction_total = float(deduction_totals.get(supplier_norm, 0.0))
            remaining_map[supplier_norm] = float(stock_total + refund_debit - allocation_total - paid_total - deduction_total)

        missing = [name for name in unique_names if name not in remaining_map]
        for name in missing:
            remaining_map[name] = float(calculate_consolidated_supplier_remaining_balance(name) or 0.0)

        return remaining_map
    except Exception:
        return {name: float(calculate_consolidated_supplier_remaining_balance(name) or 0.0) for name in unique_names}

def build_consolidated_supplier_choices():
    """Build supplier select choices with live cross-mineral remaining balances."""
    try:
        from config import db
        from sqlalchemy import func
        from copper.models import CopperSupplier, CopperStock, SupplierPayment as CopperSupplierPayment
        from cassiterite.models import CassiteriteSupplier, CassiteriteStock, CassiteriteSupplierPayment
        from core.models import UnifiedSupplierAdvance

        seen = set()
        supplier_names = []

        def add_rows(rows):
            for (name,) in rows or []:
                clean = (name or '').strip()
                if not clean:
                    continue
                key = normalize_counterparty_name(clean)
                if not key or key in seen:
                    continue
                seen.add(key)
                supplier_names.append(clean)

        add_rows(
            db.session.query(CopperSupplier.name)
            .filter(CopperSupplier.is_deleted.is_(False))
            .order_by(CopperSupplier.name.asc())
            .all()
        )
        add_rows(
            db.session.query(CassiteriteSupplier.name)
            .filter(CassiteriteSupplier.is_deleted.is_(False))
            .order_by(CassiteriteSupplier.name.asc())
            .all()
        )
        add_rows(
            db.session.query(func.trim(CopperStock.supplier))
            .filter(
                CopperStock.is_deleted.is_(False),
                func.trim(CopperStock.supplier).isnot(None),
                func.trim(CopperStock.supplier) != '',
            )
            .group_by(func.trim(CopperStock.supplier))
            .order_by(func.trim(CopperStock.supplier).asc())
            .all()
        )
        add_rows(
            db.session.query(func.trim(CassiteriteStock.supplier))
            .filter(
                CassiteriteStock.is_deleted.is_(False),
                func.trim(CassiteriteStock.supplier).isnot(None),
                func.trim(CassiteriteStock.supplier) != '',
            )
            .group_by(func.trim(CassiteriteStock.supplier))
            .order_by(func.trim(CassiteriteStock.supplier).asc())
            .all()
        )
        add_rows(
            db.session.query(UnifiedSupplierAdvance.supplier_name)
            .filter(
                UnifiedSupplierAdvance.is_deleted.is_(False),
                UnifiedSupplierAdvance.supplier_name.isnot(None),
                func.trim(UnifiedSupplierAdvance.supplier_name) != '',
            )
            .group_by(UnifiedSupplierAdvance.supplier_name)
            .order_by(UnifiedSupplierAdvance.supplier_name.asc())
            .all()
        )
        add_rows(
            db.session.query(CopperSupplierPayment.supplier_name)
            .filter(
                CopperSupplierPayment.is_deleted.is_(False),
                CopperSupplierPayment.supplier_name.isnot(None),
                func.trim(CopperSupplierPayment.supplier_name) != '',
            )
            .group_by(CopperSupplierPayment.supplier_name)
            .order_by(CopperSupplierPayment.supplier_name.asc())
            .all()
        )
        add_rows(
            db.session.query(CassiteriteSupplierPayment.supplier_name)
            .filter(
                CassiteriteSupplierPayment.is_deleted.is_(False),
                CassiteriteSupplierPayment.supplier_name.isnot(None),
                func.trim(CassiteriteSupplierPayment.supplier_name) != '',
            )
            .group_by(CassiteriteSupplierPayment.supplier_name)
            .order_by(CassiteriteSupplierPayment.supplier_name.asc())
            .all()
        )

        supplier_names = sorted(supplier_names, key=lambda item: item.lower())
        remaining_map = calculate_consolidated_supplier_remaining_balances(supplier_names)
        choices = [('', 'Select existing supplier')]
        for name in supplier_names:
            normalized = normalize_counterparty_name(name)
            remaining = float(remaining_map.get(normalized, 0.0))
            choices.append((name, f"{name} [{remaining:,.2f} RWF]"))
        return choices
    except Exception:
        return [('', 'Select existing supplier')]


def make_slug(name: str) -> str:
    """Return a URL-friendly slug for supplier names.

    Uses the same normalization rules as `normalize_counterparty_name` but
    joins words with hyphens so slugs are safe in URLs (no spaces or slashes).
    """
    normalized = normalize_counterparty_name(name)
    if not normalized:
        return ''
    return '-'.join(normalized.split())


def trace_time(func):
    """Decorator to log execution time of functions.

    Usage:
        @trace_time
        def heavy():
            ...
    Logs an INFO line with elapsed seconds after the call. If an exception
    occurs, logs the full traceback using `current_app.logger.exception` when
    available, otherwise falls back to the module logger.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except Exception:
            # Prefer app logger when running inside Flask app context
            try:
                current_app.logger.exception("Exception in %s", func.__name__)
            except Exception:
                logger.exception("Exception in %s", func.__name__)
            raise

        # Helper to log elapsed time (use app logger when available)
        def _log_elapsed(s, e):
            try:
                try:
                    current_app.logger.info("TIMER: %s took %.4f seconds", func.__name__, e - s)
                except Exception:
                    logger.info("TIMER: %s took %.4f seconds", func.__name__, e - s)
            except Exception:
                pass

        # If the result is a generator/iterator (streaming response), wrap
        # iteration so we measure time until the generator is exhausted.
        try:
            import inspect
            import types
            from flask import Response

            # Async functions are not handled by this synchronous wrapper;
            # detect coroutine functions above and avoid wrapping here.
            if inspect.isgenerator(result) or isinstance(result, types.GeneratorType):
                def gen():
                    try:
                        for item in result:
                            yield item
                    finally:
                        end2 = time.perf_counter()
                        _log_elapsed(start, end2)

                return gen()

            # If it's a Flask/Werkzeug Response with an iterable body, wrap its
            # iterable so we measure the time until the response body has been
            # fully iterated by the WSGI server.
            if isinstance(result, Response):
                try:
                    orig_iter = result.response
                    if orig_iter is None:
                        # Nothing to iterate; log immediately
                        end = time.perf_counter()
                        _log_elapsed(start, end)
                        return result

                    def wrapped_iterable():
                        try:
                            for chunk in orig_iter:
                                yield chunk
                        finally:
                            end2 = time.perf_counter()
                            _log_elapsed(start, end2)

                    # Replace the response iterable with our wrapped iterable
                    result.response = wrapped_iterable()
                except Exception:
                    # If anything goes wrong wrapping the response, log now
                    end = time.perf_counter()
                    _log_elapsed(start, end)
                return result

        except Exception:
            # Best-effort: if our inspection/wrapping fails, still log elapsed
            try:
                end = time.perf_counter()
                _log_elapsed(start, end)
            except Exception:
                pass

        # Default case: normal synchronous return value — log now.
        end = time.perf_counter()
        _log_elapsed(start, end)
        return result

    # Support async coroutine functions by returning an async wrapper
    try:
        import inspect
        if inspect.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    res = await func(*args, **kwargs)
                except Exception:
                    try:
                        current_app.logger.exception("Exception in %s", func.__name__)
                    except Exception:
                        logger.exception("Exception in %s", func.__name__)
                    raise
                finally:
                    end = time.perf_counter()
                    try:
                        try:
                            current_app.logger.info("TIMER: %s took %.4f seconds", func.__name__, end - start)
                        except Exception:
                            logger.info("TIMER: %s took %.4f seconds", func.__name__, end - start)
                    except Exception:
                        pass
                return res

            return async_wrapper
    except Exception:
        pass

    return wrapper


def update_stock(stock_id):
    """Small debug helper to trace stock calculation steps.

    Replace or call this from real update functions where you need
    per-stock tracing.
    """
    logger.debug("Starting calculation for stock %s", stock_id)
    try:
        # placeholder for calculation logic
        logger.info("Successfully updated stock %s", stock_id)
    except Exception as e:
        logger.error("Failed to update stock %s: %s", stock_id, e)


def calculate_unit_percentage(local_balance, percentage):
    if local_balance is None or percentage is None:
        return 0  # or return None based on business logic
    return local_balance * percentage

def calculate_moyenne(stocks):
    """
    Calculate MOYENNE = sum(unit%) / sum(balance)
    stocks: list of stock records
    """
    total_unit_percent = sum([s.unit_percent for s in stocks])
    total_balance = sum([s.input_kg for s in stocks])
    if total_balance == 0:
        return 0
    return total_unit_percent / total_balance

def calculate_net_balance(stock):
    """
    Calculate NET BALANCE = AMOUNT - TOT.AMOUNT TAG - RMA - INKOMANE - 3%RRA
    """
    return (
    (stock.amount or 0)
    - (stock.tot_amount_tag or 0)
    - (stock.rma or 0)
    - (stock.inkomane or 0)
    - (stock.rra_3_percent or 0)
)


def calculate_total_balance(stocks):
    """
    Rolling sum of NET BALANCE for all previous stocks
    """
    total = 0
    for s in stocks:
        total += s.net_balance
    return total


from threading import Thread


# --- Brevo / sib-api-v3-sdk transactional email helper ---
try:
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException
    from sib_api_v3_sdk.models.send_smtp_email import SendSmtpEmail
except Exception:
    sib_api_v3_sdk = None
    ApiException = Exception
    SendSmtpEmail = None


def _init_brevo_client():
    """Create and return a TransactionalEmailsApi instance or None.

    Uses the `BREVO_API_KEY` env var. Returns (api_instance, error_message)
    where api_instance is None on failure.
    """
    try:
        api_key = os.getenv('BREVO_API_KEY')
        if not api_key or not sib_api_v3_sdk:
            return None, 'BREVO_API_KEY not set or sib-api-v3-sdk not installed'

        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = api_key
        api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
        return api, None
    except Exception as e:
        return None, str(e)


# Initialize at import time if possible (non-fatal if not configured)
_BREVO_API, _BREVO_ERR = _init_brevo_client()


def send_brevo_email(subject, html_content, to_emails, sender_email=None, sender_name='Urumuli Smart System'):
    """Send email via Brevo Transactional API synchronously.

    Returns True on success, False on failure.
    """
    try:
        # If the module-level client wasn't initialized at import time (for
        # example the process started before .env was loaded), try to initialize
        # it now. This avoids the "Brevo API not configured" race on first use.
        global _BREVO_API, _BREVO_ERR
        if not _BREVO_API:
            _BREVO_API, _BREVO_ERR = _init_brevo_client()
            if not _BREVO_API:
                try:
                    current_app.logger.warning("Brevo API not configured (late init): %s", _BREVO_ERR)
                except Exception:
                    logging.warning("Brevo API not configured (late init): %s", _BREVO_ERR)
                return False

        sender = {
            'name': sender_name,
            'email': sender_email or os.getenv('BREVO_SENDER_EMAIL') or 'no-reply@example.com'
        }

        send_smtp = SendSmtpEmail(
            to=[{"email": e} for e in to_emails],
            sender=sender,
            subject=subject,
            html_content=html_content,
        )

        resp = _BREVO_API.send_transac_email(send_smtp)
        try:
            current_app.logger.info("Brevo email sent: %s", getattr(resp, 'messageId', resp))
        except Exception:
            logging.info("Brevo email sent: %s", resp)
        return True
    except ApiException as e:
        try:
            current_app.logger.exception("Brevo ApiException sending email: %s", e)
        except Exception:
            logging.exception("Brevo ApiException sending email: %s", e)
        return False
    except Exception as e:
        try:
            current_app.logger.exception("Unexpected error sending Brevo email: %s", e)
        except Exception:
            logging.exception("Unexpected error sending Brevo email: %s", e)
        return False


def send_brevo_email_async(subject, html_content, to_emails, sender_email=None, sender_name='Urumuli Smart System'):
    """Non-blocking wrapper that sends Brevo emails in a daemon thread."""
    try:
        try:
            current_app.logger.info("ENQUEUE BREVO: to=%s subject=%s", to_emails, subject)
        except Exception:
            logging.info("ENQUEUE BREVO: to=%s subject=%s", to_emails, subject)
        t = Thread(target=send_brevo_email, args=(subject, html_content, to_emails, sender_email, sender_name), daemon=True)
        t.start()
    except Exception:
        try:
            current_app.logger.exception("Failed to spawn Brevo mail thread")
        except Exception:
            logging.exception("Failed to spawn Brevo mail thread")
