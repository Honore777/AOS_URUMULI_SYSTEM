"""
Cassiterite Supplier Payment Model
Records supplier payments for cassiterite stock obligations.
"""
from datetime import datetime
from config import db


class CassiteriteSupplierPayment(db.Model):
    """
    Records supplier payments for cassiterite stock obligations.
    """
    __tablename__ = 'cassiterite_supplier_payment'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(
        db.Integer,
        db.ForeignKey('cassiterite_stock.id'),
        nullable=False,
        index=True,
    )
    
    amount = db.Column(db.Float, nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    method = db.Column(db.String(50))  # cash, bank, momo
    reference = db.Column(db.String(100))  # receipt / transaction id
    note = db.Column(db.Text)

    def __repr__(self):
        return f"<CassiteriteSupplierPayment {self.amount} RWF for Stock {self.stock_id}>"
