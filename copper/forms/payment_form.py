"""
Supplier Payment Form
For recording supplier payments
"""
from flask_wtf import FlaskForm
from wtforms import SelectField, FloatField, StringField, TextAreaField, SubmitField
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, InputRequired, NumberRange, Optional, ValidationError


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
        validators=[Optional()],
        validate_choice=False,
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
        default=None,
    )
    paid_at = DateTimeLocalField(
        'Payment Date / Time',
        format='%Y-%m-%dT%H:%M',
        validators=[Optional()],
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

    def validate_exchange_rate(self, field):
        """Require a positive exchange rate for USD payments."""
        currency = (self.currency.data or 'RWF').upper()
        if currency != 'USD':
            return
        if field.data is None:
            raise ValidationError('Exchange rate is required for USD payments.')
        try:
            if float(field.data) <= 0:
                raise ValidationError('Exchange rate must be greater than 0 for USD payments.')
        except (TypeError, ValueError):
            raise ValidationError('Exchange rate is required for USD payments.')
