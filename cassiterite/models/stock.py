"""
Cassiterite Stock Model
Lightweight, per-row calculations are kept here. Global aggregates are
maintained via a single-row `StockAggregate` and delta updates to keep
add/edit/delete/output operations O(1).
"""
from datetime import datetime
from config import db
from sqlalchemy import func, or_
from utils import calculate_unit_percentage, calculate_net_balance, trace_time, logger
from core.models import StockAggregate
import os


class CassiteriteStock(db.Model):
    __tablename__ = 'cassiterite_stock'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    voucher_no = db.Column(db.String(100), unique=True, nullable=False)
    supplier = db.Column(db.String(100), nullable=False)

    # Input & Basic Calculations
    input_kg = db.Column(db.Float)
    percentage = db.Column(db.Float)
    u = db.Column(db.Float)

    # Cassiterite-specific
    lme = db.Column(db.Float)
    m_lme = db.Column(db.Float)
    sec = db.Column(db.Float)
    tc = db.Column(db.Float)

    # Pricing
    u_price = db.Column(db.Float)
    exchange = db.Column(db.Float)
    transport_tag = db.Column(db.Float)

    # Calculations
    amount = db.Column(db.Float)
    amount_with_taxes = db.Column(db.Float)
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
    balance_to_pay = db.Column(db.Float)
    moyenne = db.Column(db.Float, default=0)

    # Soft delete fields for auditability
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)
    delete_reason = db.Column(db.Text, nullable=True)

    # Relationships are defined elsewhere (outputs, payments)
    # Define lightweight relationships for convenience and back_populates
    outputs = db.relationship(
        'CassiteriteOutput',
        back_populates='stock',
        foreign_keys='CassiteriteOutput.stock_id',
        lazy=True,
        cascade='all, delete-orphan',
    )

    payments = db.relationship(
        'CassiteriteSupplierPayment',
        backref='stock',
        lazy=True,
        cascade='all, delete-orphan',
    )

    advance_allocations = db.relationship(
        'CassiteriteAdvanceAllocation',
        backref='stock',
        foreign_keys='CassiteriteAdvanceAllocation.stock_id',
        lazy=True,
        cascade='all, delete-orphan',
    )

    def __repr__(self):
        return f"<CassiteriteStock {self.voucher_no} - {self.supplier}>"

    def remaining_stock(self):
        from .output import CassiteriteOutput
        outputs_total = db.session.query(func.coalesce(func.sum(CassiteriteOutput.output_kg), 0)).filter(CassiteriteOutput.stock_id == self.id).scalar() or 0
        return (self.input_kg or 0) - outputs_total

    def remaining_to_pay(self):
        from .payment import CassiteriteSupplierPayment, CassiteriteAdvanceAllocation
        total_paid = float(
            db.session.query(
                func.coalesce(
                    func.sum(func.coalesce(CassiteriteSupplierPayment.amount_rwf, CassiteriteSupplierPayment.amount)),
                    0,
                )
            )
            .filter(
                CassiteriteSupplierPayment.stock_id == self.id,
                CassiteriteSupplierPayment.is_deleted.is_(False),
            )
            .scalar()
            or 0
        )

        advance_applied = 0.0
        try:
            # Best-effort: if the allocation table is not migrated yet, Postgres will
            # abort the transaction; we MUST rollback so the rest of the page can load.
            advance_applied = (
                db.session.query(func.coalesce(func.sum(CassiteriteAdvanceAllocation.applied_amount), 0))
                .filter(CassiteriteAdvanceAllocation.stock_id == self.id)
                .scalar()
                or 0
            )
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            advance_applied = 0.0

        unified_applied = 0.0
        try:
            from core.models import UnifiedSupplierAdvanceAllocation
            unified_applied = (
                db.session.query(func.coalesce(func.sum(UnifiedSupplierAdvanceAllocation.applied_amount), 0))
                .filter(
                    UnifiedSupplierAdvanceAllocation.stock_mineral_type == 'cassiterite',
                    UnifiedSupplierAdvanceAllocation.stock_id == self.id,
                )
                .scalar()
                or 0
            )
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            unified_applied = 0.0

        # Backward compatibility: if unified allocations are not present for
        # this stock, fall back to the legacy allocation table.
        if float(unified_applied or 0.0) > 0.0:
            advance_applied = 0.0

        base_balance = float(self.balance_to_pay or self.net_balance or 0)
        return max(base_balance - float(total_paid or 0.0) - float(advance_applied or 0.0) - float(unified_applied or 0.0), 0.0)

    @trace_time
    def update_calculations(self):
        """Recompute per-row derived fields only.

        This method intentionally avoids performing a full-table SUM
        or writing the global aggregate. Global state is maintained by
        delta updates through `apply_aggregate_delta` in the routes.
        """
        try:
            for field in ['input_kg', 'tot_amount_tag', 'rma', 'inkomane', 'percentage', 'lme', 'sec', 'tc', 'transport_tag', 'exchange']:
                setattr(self, field, getattr(self, field) or 0.0)

            # per-row fields
            self.local_balance = self.remaining_stock()
            self.unit_percent = calculate_unit_percentage(self.local_balance, self.percentage)
            self.t_unity = self.unit_percent
            self.u_price = ((self.lme or 0) - (self.sec or 0)) * (self.percentage or 0) / 100
            self.amount = ((self.u_price or 0) - (self.tc or 0)) / 1000
            self.amount_with_taxes = (self.amount or 0) * (self.exchange or 0) * (self.input_kg or 0)
            self.tot_amount_tag = (self.transport_tag or 0) * (self.input_kg or 0)
            rra_base = (((self.lme or 0) * (self.percentage or 0) / 100) - 100) / 1000
            self.rra_3_percent = (rra_base * (self.exchange or 0) * (self.input_kg or 0) * 3) / 100
            self.balance_to_pay = (self.amount_with_taxes or 0) - (self.tot_amount_tag or 0) - (self.rma or 0) - (self.inkomane or 0) - (self.rra_3_percent or 0)
            self.net_balance = self.balance_to_pay

            # rolling cumulative values (DB-side SUM of prior rows)
            prev_balance_q = db.session.query(func.coalesce(func.sum(CassiteriteStock.net_balance), 0)).filter(
                or_(
                    CassiteriteStock.date < self.date,
                    (CassiteriteStock.date == self.date) & (CassiteriteStock.id < (self.id or 0))
                ),
                CassiteriteStock.local_balance > 0
            )
            previous_total_balance = prev_balance_q.scalar() or 0
            self.total_balance = previous_total_balance + (self.net_balance or 0)

            prev_local_q = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(
                or_(
                    CassiteriteStock.date < self.date,
                    (CassiteriteStock.date == self.date) & (CassiteriteStock.id < (self.id or 0))
                ),
                CassiteriteStock.local_balance > 0
            )
            previous_total_local = prev_local_q.scalar() or 0
            self.total_local_balance = previous_total_local + (self.local_balance or 0)

            # Read-only: set the instance moyenne from the lightweight aggregate
            try:
                agg = StockAggregate.get('cassiterite')
                if agg and agg.total_quantity:
                    self.moyenne = float(agg.total_weighted_percent or 0.0) / float(agg.total_quantity or 1.0)
                else:
                    self.moyenne = self.moyenne or 0
            except Exception:
                try:
                    logger.exception("update_calculations: failed to read StockAggregate")
                except Exception:
                    pass
        except Exception:
            try:
                logger.exception("update_calculations failed for CassiteriteStock id=%s", getattr(self, 'id', None))
            except Exception:
                pass
            raise

    @staticmethod
    def update_global_moyennes():
        """Recompute and write the single-row aggregate for cassiterite.

        This method is intended for backfills/repairs or when you explicitly
        want to force a full recalculation. Normal add/edit/delete/output
        flows should use delta updates instead.
        """
        total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
        total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
        total_t_unity = db.session.query(func.coalesce(func.sum(CassiteriteStock.t_unity), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0

        total_unit_percent = float(total_unit_percent or 0.0)
        total_remaining_balance = float(total_remaining_balance or 0.0)
        total_t_unity = float(total_t_unity or 0.0)

        if not total_remaining_balance:
            moyenne = 0
        else:
            moyenne = total_unit_percent / total_remaining_balance

        try:
            agg = db.session.query(StockAggregate).filter_by(mineral_type='cassiterite').with_for_update().first()
            if not agg:
                agg = StockAggregate(mineral_type='cassiterite', total_quantity=total_remaining_balance, total_weighted_percent=total_unit_percent, total_t_unity=total_t_unity)
                db.session.add(agg)
            else:
                agg.total_quantity = total_remaining_balance
                agg.total_weighted_percent = total_unit_percent
                agg.total_t_unity = total_t_unity

            if os.environ.get('UPDATE_PER_ROW_MOYENNE', '0').lower() in ('1', 'true', 'yes'):
                db.session.query(CassiteriteStock).update({
                    CassiteriteStock.moyenne: moyenne,
                }, synchronize_session=False)
        except Exception:
            try:
                db.session.query(CassiteriteStock).update({
                    CassiteriteStock.moyenne: moyenne,
                }, synchronize_session=False)
            except Exception:
                logger.exception("update_global_moyennes: failed to update moyenne")

    @staticmethod
    def contribution(stock_obj) -> tuple:
        """Return the per-stock contribution triple: (quantity, weighted_percent, t_unity)."""
        return (
            float(stock_obj.local_balance or 0.0),
            float(stock_obj.unit_percent or 0.0),
            float(stock_obj.t_unity or 0.0),
        )

    @staticmethod
    def apply_aggregate_delta(delta_q: float, delta_wp: float, delta_t: float, mineral_type: str = 'cassiterite'):
        """Apply a delta to the single-row StockAggregate within the current transaction.

        Uses SELECT ... FOR UPDATE to serialize concurrent updates.
        """
        try:
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
        """Full recompute of the stock aggregate (safety/repair)."""
        try:
            total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
            total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0
            total_t_unity = db.session.query(func.coalesce(func.sum(CassiteriteStock.t_unity), 0)).filter(CassiteriteStock.local_balance > 0).scalar() or 0

            agg = db.session.query(StockAggregate).filter_by(mineral_type='cassiterite').with_for_update().first()
            if not agg:
                agg = StockAggregate(mineral_type='cassiterite', total_quantity=float(total_remaining_balance), total_weighted_percent=float(total_unit_percent), total_t_unity=float(total_t_unity))
                db.session.add(agg)
            else:
                agg.total_quantity = float(total_remaining_balance)
                agg.total_weighted_percent = float(total_unit_percent)
                agg.total_t_unity = float(total_t_unity)
            db.session.flush()
            return agg
        except Exception:
            logger.exception("rebuild_stock_aggregate failed for cassiterite")
            return None


# Backwards-compatible module-level helper
def rebuild_stock_aggregate():
    return CassiteriteStock.rebuild_stock_aggregate()
