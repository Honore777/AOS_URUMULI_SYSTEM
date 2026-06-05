"""
Copper Stock Model
Represents each incoming copper stock record.
Stores quantities, suppliers, and derived calculations.
"""
from datetime import datetime
from config import db
from sqlalchemy import func
from sqlalchemy.orm import backref
from utils import calculate_unit_percentage, calculate_net_balance, calculate_moyenne, logger
import os


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "False").lower() in ("1", "true", "yes")


class CopperStock(db.Model):
    """
    Represents each incoming copper stock record.
    Stores quantities, suppliers, and derived calculations.
    """
    __tablename__ = 'copper_stock'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    voucher_no = db.Column(db.String(100), unique=True, nullable=False)
    supplier = db.Column(db.String(100), nullable=False)
    
    # Input & Basic Calculations
    input_kg = db.Column(db.Float)
    percentage = db.Column(db.Float)
    nb = db.Column(db.Float)
    u = db.Column(db.Float)
    
    # Pricing
    u_price = db.Column(db.Float)
    exchange = db.Column(db.Float)
    transport_tag = db.Column(db.Float)
    
    # Calculations
    amount = db.Column(db.Float)
    tot_amount_tag = db.Column(db.Float)
    rma = db.Column(db.Float)
    inkomane = db.Column(db.Float)
    rra_3_percent = db.Column(db.Float)
    
    # Balances & Averages
    local_balance = db.Column(db.Float, default=0)
    total_local_balance = db.Column(db.Float)
    unit_percent = db.Column(db.Float)
    t_unity = db.Column(db.Float)
    net_balance = db.Column(db.Float)
    total_balance = db.Column(db.Float)
    moyenne = db.Column(db.Float, default=0)
    moyenne_nb = db.Column(db.Float, default=0)

    # Soft delete fields for auditability
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)
    delete_reason = db.Column(db.Text, nullable=True)

    # Composite index for common query patterns (optimization, filtering)
    __table_args__ = (
        db.Index('idx_copper_stock_is_deleted_id', 'is_deleted', 'id'),
    )

    # Relationships
    outputs = db.relationship('CopperOutput', 
                             back_populates='stock', 
                             foreign_keys='CopperOutput.stock_id',
                             lazy=True, 
                             cascade="all, delete-orphan")
    supplier_payments = db.relationship(
        'SupplierPayment',
        backref=backref('stock', lazy=True, foreign_keys='SupplierPayment.stock_id'),
        foreign_keys='SupplierPayment.stock_id',
        lazy=True,
        cascade="all, delete-orphan"
    )
    advance_allocations = db.relationship(
        'CopperAdvanceAllocation',
        backref='stock',
        foreign_keys='CopperAdvanceAllocation.stock_id',
        lazy=True,
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<CopperStock {self.voucher_no} - {self.supplier}>"

    def remaining_stock(self):
        """Calculate remaining stock after outputs"""
        # Use a DB-side aggregate to avoid loading all output rows into Python
        from .output import CopperOutput
        outputs_total = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).filter(CopperOutput.stock_id == self.id).scalar() or 0
        return (self.input_kg or 0) - outputs_total

    def remaining_to_pay(self):
        """Calculate remaining amount to pay supplier"""
        # Use DB-side aggregates to avoid loading payment rows into Python.
        from .payment import SupplierPayment, CopperAdvanceAllocation
        total_paid = float(db.session.query(func.coalesce(func.sum(SupplierPayment.amount_rwf), 0)).filter(
            SupplierPayment.stock_id == self.id,
            SupplierPayment.is_deleted.is_(False),
        ).scalar() or 0)
        unified_applied = 0.0
        try:
            from core.models import UnifiedSupplierAdvanceAllocation
            unified_applied = (
                db.session.query(func.coalesce(func.sum(UnifiedSupplierAdvanceAllocation.applied_amount), 0))
                .filter(
                    UnifiedSupplierAdvanceAllocation.stock_mineral_type == 'copper',
                    UnifiedSupplierAdvanceAllocation.stock_id == self.id,
                )
                .scalar()
                or 0.0
            )
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            unified_applied = 0.0

        # Backward compatibility: if the unified allocation layer is not yet
        # populated for this stock, fall back to the legacy allocation table.
        advance_applied = 0.0
        if float(unified_applied or 0.0) <= 0.0:
            advance_applied = db.session.query(func.coalesce(func.sum(CopperAdvanceAllocation.applied_amount), 0)).filter(
                CopperAdvanceAllocation.stock_id == self.id,
            ).scalar() or 0.0

        base_balance = float(self.net_balance or 0)
        return max(base_balance - float(total_paid or 0.0) - float(advance_applied or 0.0) - float(unified_applied or 0.0), 0.0)

    def update_calculations(self):
        """
        Recalculate all computed fields for this stock using DB-side aggregates
        where possible. This avoids loading all previous rows into Python and
        ensures we only consider stocks with `local_balance > 0` when computing
        cumulative and moyenne figures.
        """
        # Step 1: ensure defaults
        for field in ['input_kg', 'amount', 'tot_amount_tag', 'rma', 'inkomane', 'nb', 'percentage']:
            setattr(self, field, getattr(self, field) or 0.0)

        # Step 2: calculate local balance
        self.local_balance = self.remaining_stock()

        # Step 3: calculate unit %
        self.unit_percent = calculate_unit_percentage(self.local_balance, self.percentage)

        # Step 4: t_unity formula
        self.t_unity = (self.nb or 0) * (self.local_balance or 0)

        # Step 5: net balance
        self.net_balance = calculate_net_balance(self)

        # NOTE: We no longer perform full-table SUM(...) queries here.
        # This method now only computes per-row derived fields (local_balance,
        # unit_percent, t_unity, net_balance). Global aggregates (moyenne)
        # are maintained using delta-updates to the single-row
        # `StockAggregate` in the routes that perform add/edit/delete/output
        # operations. This avoids O(N) writes on each change.

        # Clear cumulative fields used historically; they will be computed
        # at read-time (windowed query) when needed.
        self.total_balance = None
        self.total_local_balance = None

        # Read-only: set current globale moyenne values from the lightweight
        # StockAggregate so templates can display them immediately.
        try:
            from core.models import StockAggregate
            agg = StockAggregate.get('copper')
            if agg and agg.total_quantity:
                self.moyenne = agg.total_weighted_percent / agg.total_quantity
                self.moyenne_nb = agg.total_t_unity / agg.total_quantity
            else:
                self.moyenne = self.moyenne or 0
                self.moyenne_nb = self.moyenne_nb or 0
        except Exception:
            # best-effort: preserve existing instance values on error
            try:
                logger.exception("update_calculations: failed to read StockAggregate")
            except Exception:
                pass

    @staticmethod
    def update_global_moyennes():
        """Recalculate MOYENNE and MOYENNE_NB across all remaining copper stocks."""
        # New behaviour: do not compute SUM(...) here. Instead, read the
        # lightweight single-row `StockAggregate` and return its derived
        # moyenne values. For repairs or initial population call
        # `rebuild_stock_aggregate()` which performs a full SUM.
        try:
            from core.models import StockAggregate
            agg = StockAggregate.get('copper')
            if not agg:
                # no aggregate present — indicate zero averages
                return 0, 0
            if not agg.total_quantity:
                return 0, 0
            moyenne = float(agg.total_weighted_percent or 0.0) / float(agg.total_quantity or 1.0)
            moyenne_nb = float(agg.total_t_unity or 0.0) / float(agg.total_quantity or 1.0)
            return moyenne, moyenne_nb
        except Exception:
            try:
                logger.exception("update_global_moyennes: failed to read aggregate")
            except Exception:
                pass
            return 0, 0

    @staticmethod
    def contribution(stock_obj) -> tuple:
        """Return the stock's contribution triple used for aggregate deltas.

        Returns (quantity, weighted_percent, t_unity) — these values are
        already stored in `unit_percent` and `t_unity` fields (unit_percent
        is local_balance * percentage).
        """
        return (
            float(stock_obj.local_balance or 0.0),
            float(stock_obj.unit_percent or 0.0),
            float(stock_obj.t_unity or 0.0),
        )

    @staticmethod
    def apply_aggregate_delta(delta_q: float, delta_wp: float, delta_t: float, mineral_type: str = 'copper'):
        """Apply a delta to the single-row StockAggregate inside the
        current transaction. Uses SELECT ... FOR UPDATE to avoid races.
        """
        try:
            from core.models import StockAggregate

            agg = db.session.query(StockAggregate).filter_by(mineral_type=mineral_type).with_for_update().first()
            if not agg:
                agg = StockAggregate(mineral_type=mineral_type, total_quantity=0.0, total_weighted_percent=0.0, total_t_unity=0.0)
                db.session.add(agg)
                db.session.flush()

            agg.total_quantity = float((agg.total_quantity or 0.0) + (delta_q or 0.0))
            agg.total_weighted_percent = float((agg.total_weighted_percent or 0.0) + (delta_wp or 0.0))
            agg.total_t_unity = float((agg.total_t_unity or 0.0) + (delta_t or 0.0))
            db.session.flush()
            return agg
        except Exception:
            try:
                logger.exception("apply_aggregate_delta failed for %s", mineral_type)
            except Exception:
                pass
            raise

    @staticmethod
    def rebuild_stock_aggregate():
        """Full recompute of the stock aggregate (safety/repair).

        This executes SUM(...) queries and overwrites the single-row
        `StockAggregate`. Use sparingly (backfill/repair).
        """
        try:
            total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
            total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(CopperStock.local_balance > 0).scalar() or 0
            total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(CopperStock.local_balance > 0).scalar() or 0

            from core.models import StockAggregate
            agg = db.session.query(StockAggregate).filter_by(mineral_type='copper').with_for_update().first()
            if not agg:
                agg = StockAggregate(mineral_type='copper', total_quantity=float(total_remaining_balance), total_weighted_percent=float(total_unit_percent), total_t_unity=float(total_t_unity))
                db.session.add(agg)
            else:
                agg.total_quantity = float(total_remaining_balance)
                agg.total_weighted_percent = float(total_unit_percent)
                agg.total_t_unity = float(total_t_unity)
            db.session.flush()
            return agg
        except Exception:
            try:
                logger.exception("rebuild_stock_aggregate failed for copper")
            except Exception:
                pass
            return None
