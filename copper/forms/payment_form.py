"""
Supplier Payment Form
For recording supplier payments
"""
from flask_wtf import FlaskForm
from wtforms import SelectField, FloatField, StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, InputRequired, NumberRange, Optional


class SupplierPaymentForm(FlaskForm):
    """Form for recording supplier payments"""
    payment_kind = SelectField(
        'Payment Type',
        choices=[
            ('settlement', 'Settle Existing Supplier Debt'),
            ('advance', 'Pay Supplier Advance'),
        ],
        validators=[DataRequired()],
        default='settlement',
    )
    existing_supplier = SelectField(
        'Existing Supplier (for advance)',
        choices=[],
        validators=[Optional()],
    )
    new_supplier = StringField(
        'Or New Supplier Name (for advance)',
        validators=[Optional()],
    )
    stock_id = SelectField(
        'Select Supplier Obligation',
        coerce=int,
        validators=[Optional()]
    )
    amount = FloatField(
        'Payment amount',
        validators=[InputRequired(), NumberRange(min=0.01)]
    )
    currency = SelectField(
        'Currency',
        choices=[('RWF', 'RWF'), ('USD', 'USD')],
        validators=[DataRequired()],
        default='RWF',
    )
    exchange_rate = FloatField(
        'Exchange Rate (RWF per currency unit)',
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1.0,
    )
    method = SelectField(
        'Payment Method',
        choices=[
            ('cash', 'Cash'),
            ('bank', 'Bank Transfer'),
            ('momo', 'Mobile Money')
        ],
        validators=[DataRequired()]
    )
    reference = StringField(
        'Payment Reference',
        validators=[Optional()]
    )
    note = TextAreaField(
        'Note / Reason',
        validators=[Optional()]
    )
    # When editing or deleting an existing payment, the accountant must
    # provide a short reason explaining the change. Routes will enforce
    # that this is present for edit/delete operations.
    change_reason = TextAreaField(
        'Change reason (required when editing/deleting)',
        validators=[Optional()]
    )
    submit = SubmitField('Record Payment')
