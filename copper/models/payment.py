"""
Supplier Payment Model
Records supplier payments for copper stock obligations.
"""
from datetime import datetime
from config import db
from sqlalchemy.orm import backref


class CopperSupplier(db.Model):
    """Copper supplier master table (mineral-scoped identity)."""
    __tablename__ = 'copper_supplier'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True, unique=True)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    # Soft delete fields for auditability
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)
    delete_reason = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<CopperSupplier {self.name}>"


class SupplierPayment(db.Model):
    """
    Records supplier payments for copper stock obligations.
    """
    __tablename__ = 'supplier_payment'

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(
        db.Integer,
        db.ForeignKey('copper_supplier.id'),
        nullable=True,
        index=True,
    )
    stock_id = db.Column(
        db.Integer,
        db.ForeignKey('copper_stock.id'),
        nullable=True,
        index=True,
    )
    
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    # Original payment amount entered by user (can be USD or RWF).
    input_amount = db.Column(db.Numeric(18, 2), nullable=True)
    currency = db.Column(db.String(10), nullable=False, default='RWF', index=True)
    # Exchange rate used at transaction time (RWF per 1 unit of currency).
    exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
    # Normalized amount used for obligations, debt and gross-profit math.
    amount_rwf = db.Column(db.Numeric(18, 2), nullable=False, default=0.0, index=True)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    method = db.Column(db.String(50))  # cash, bank, momo
    reference = db.Column(db.String(100))  # receipt / transaction id
    note = db.Column(db.Text)

    # Advance-payment/audit fields (additive, backwards compatible)
    supplier_name = db.Column(db.String(100), index=True, nullable=True)
    is_advance = db.Column(db.Boolean, nullable=False, default=False, index=True)
    advance_remaining = db.Column(db.Numeric(18, 2), nullable=False, default=0.0)

    # Robust lifecycle fields for approval/disbursement workflow
    payment_type = db.Column(db.String(20), nullable=False, default='SETTLEMENT', index=True)
    approval_status = db.Column(db.String(20), nullable=False, default='PENDING', index=True)
    disbursement_status = db.Column(db.String(20), nullable=False, default='DISBURSED', index=True)
    approved_by_id = db.Column(db.Integer, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    disbursed_by_id = db.Column(db.Integer, nullable=True)
    disbursed_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, nullable=True, index=True)

    supplier = db.relationship('CopperSupplier', backref='payments', lazy=True)
    advance_allocations = db.relationship(
        'CopperAdvanceAllocation',
        backref=backref('advance_payment', lazy=True),
        foreign_keys='CopperAdvanceAllocation.supplier_payment_id',
        lazy=True,
        cascade="all, delete-orphan"
    )

    # Soft delete fields for financial auditability
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)
    delete_reason = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<SupplierPayment {self.amount_rwf or self.amount} RWF for Stock {self.stock_id}>"


class CopperAdvanceAllocation(db.Model):
    """Join table linking one supplier advance payment to one stock receipt.

    A single supplier advance can be consumed by multiple stock deliveries,
    and a single stock delivery can consume multiple advances. This table
    preserves the audit trail without overloading the stock/payment tables.
    """

    __tablename__ = 'copper_advance_allocation'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(
        db.Integer,
        db.ForeignKey('copper_stock.id'),
        nullable=False,
        index=True,
    )
    supplier_payment_id = db.Column(
        db.Integer,
        db.ForeignKey('supplier_payment.id'),
        nullable=False,
        index=True,
    )
    applied_amount = db.Column(db.Numeric(18, 2), nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<CopperAdvanceAllocation stock={self.stock_id} payment={self.supplier_payment_id} amount={self.applied_amount}>"
