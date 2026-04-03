"""
Copper Output Model
Represents copper outputs (sales or movements).
Linked to a copper stock record.
"""
from datetime import datetime
from config import db


class CopperOutput(db.Model):
    """
    Represents copper outputs (sales or movements).
    Linked to a copper stock record.
    """
    __tablename__ = 'copper_output'

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('copper_stock.id'), nullable=False, index=True)

    
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow, index=True)
    output_kg = db.Column(db.Float, nullable=False)
    batch_id=db.Column(db.String(100),nullable=True, index=True)
    customer = db.Column(db.String(100))
    output_amount = db.Column(db.Float)
    amount_paid = db.Column(db.Float, default=0)
    debt_remaining = db.Column(db.Float, default=0)
    note = db.Column(db.Text)
    voucher_no=db.Column(db.String(100), db.ForeignKey('copper_stock.voucher_no'),nullable=True)

    stock = db.relationship('CopperStock', 
                           back_populates='outputs', 
                           lazy=True, 
                           foreign_keys=[stock_id])

    def __repr__(self):
        return f"<CopperOutput {self.output_kg}kg for Stock {self.stock_id}>"

    def update_debt(self):
        """Calculate remaining debt after payment"""
        self.debt_remaining = (self.output_amount or 0) - (self.amount_paid or 0)
