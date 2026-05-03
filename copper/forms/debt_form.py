"""
Debt Tracking Form
For tracking customer debts
"""
from flask_wtf import FlaskForm
from wtforms import SelectField, FloatField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional
from config import db
from sqlalchemy import func


class DebtTrackingForm(FlaskForm):
    """Form for tracking copper customer debts"""
    customer = SelectField('Select Customer', validators=[DataRequired()])
    payment_amount = FloatField('New payment', validators=[DataRequired(), NumberRange(min=0.01, message="Amount must be greater than zero")])
    currency = SelectField('Currency', choices=[('RWF', 'RWF'), ('USD', 'USD')], validators=[DataRequired()], default='RWF')
    exchange_rate = FloatField('Exchange Rate (RWF per currency unit)', validators=[Optional(), NumberRange(min=0.0001)], default=1.0)
    submit = SubmitField('Save Payment')

    def __init__(self, *args, **kwargs):
        super(DebtTrackingForm, self).__init__(*args, **kwargs)
        from copper.models import CopperOutput
        
        customer_with_debt = (
            db.session.query(CopperOutput.customer, func.sum(CopperOutput.debt_remaining).label('total_debt'))
            .filter(CopperOutput.debt_remaining > 0)
            .group_by(CopperOutput.customer)
            .all()
        )
        self.customer.choices = [
            (c[0], f"{c[0]} - Remaining with : {c[1]}") for c in customer_with_debt
        ]
