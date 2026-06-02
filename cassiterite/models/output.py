"""
Cassiterite Output Model
Represents cassiterite outputs (sales or movements).
Linked to a cassiterite stock record.
"""
from datetime import datetime
from config import db


class CassiteriteOutput(db.Model):
    """
    Represents cassiterite outputs (sales or movements).
    Linked to a cassiterite stock record.
    """
    __tablename__ = 'cassiterite_output'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('cassiterite_stock.id'), nullable=False, index=True)

    date = db.Column(db.Date, nullable=False, default=datetime.utcnow, index=True)
    output_kg = db.Column(db.Float, nullable=False)
    batch_id = db.Column(db.String(100), nullable=True, index=True)
    customer = db.Column(db.String(100))
    output_amount = db.Column(db.Float)
    output_amount_rwf = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    amount_paid_rwf = db.Column(db.Float, default=0)
    currency = db.Column(db.String(10), nullable=False, default='RWF', index=True)
    exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
    payment_stage = db.Column(db.String(30), nullable=False, default='FULL_SETTLEMENT', index=True)
    debt_remaining = db.Column(db.Float, default=0)
    note = db.Column(db.Text)
    voucher_no = db.Column(db.String(100), db.ForeignKey('cassiterite_stock.voucher_no'), nullable=True)

    # Soft delete fields
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    stock = db.relationship('CassiteriteStock', 
                           back_populates='outputs', 
                           lazy=True, 
                           foreign_keys=[stock_id])

    def __repr__(self):
        return f"<CassiteriteOutput {self.output_kg}kg for Stock {self.stock_id}>"

    def update_debt(self):
        """Calculate remaining debt after payment"""
        sale_amount = self.output_amount_rwf if self.output_amount_rwf is not None else self.output_amount
        paid_amount = self.amount_paid_rwf if self.amount_paid_rwf is not None else self.amount_paid
        self.debt_remaining = (sale_amount or 0) - (paid_amount or 0)
