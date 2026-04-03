"""
Supplier Payment Model
Records supplier payments for copper stock obligations.
"""
from datetime import datetime
from config import db


class SupplierPayment(db.Model):
    """
    Records supplier payments for copper stock obligations.
    """
    __tablename__ = 'supplier_payment'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(
        db.Integer,
        db.ForeignKey('copper_stock.id'),
        nullable=False,
        index=True,
    )
    
    amount = db.Column(db.Float, nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    method = db.Column(db.String(50))  # cash, bank, momo
    reference = db.Column(db.String(100))  # receipt / transaction id
    note = db.Column(db.Text)

    def __repr__(self):
        return f"<SupplierPayment {self.amount} RWF for Stock {self.stock_id}>"
