from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateField, SubmitField, SelectField, TextAreaField, SelectMultipleField
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, ValidationError, NumberRange

class CassiteriteWorkerPaymentForm(FlaskForm):
    worker_name = StringField(
        'Worker Name',
        validators=[DataRequired()]
    )
    amount = FloatField(
        'Payment Amount (RWF)',
        validators=[InputRequired(), NumberRange(min=0.01)]
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
    cashier_name = StringField(
        'Cashier Name',
        validators=[Optional(), Length(max=120)]
    )
    note = TextAreaField(
        'Note / Reason',
        validators=[Optional()]
    )
    change_reason = TextAreaField(
        'Change / Delete Reason',
        validators=[Optional(), Length(max=1000)]
    )
    submit = SubmitField('Record Payment')

# Export all forms
__all__ = [
    'AddCassiteriteStockForm',
    'RecordCassiteriteOutputForm',
    'RecordCassiteritePaymentForm',
    'OptimizeCassiteriteForm',
    'CassiteriteSupplierPaymentForm',
    'CassiteriteWorkerPaymentForm'
]
"""
Cassiterite Forms
"""
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateField, SubmitField, SelectField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, ValidationError


def remove_commas(form, field):
    """Remove commas from numeric input before validation"""
    if field.data:
        try:
            field.data = float(str(field.data).replace(',', ''))
        except (ValueError, AttributeError):
            raise ValidationError(f'{field.label.text} must be a valid number')


class AddCassiteriteStockForm(FlaskForm):
    """Form to add new cassiterite stock"""
    date = DateField('Date', validators=[DataRequired()], format='%Y-%m-%d')
    voucher_no = StringField('Lot Number', validators=[DataRequired(), Length(min=2, max=100)])
    supplier = StringField('Supplier', validators=[DataRequired(), Length(min=2, max=100)])
    input_kg = FloatField('Input (kg)', validators=[InputRequired()])
    percentage = FloatField('Percentage (%)', validators=[InputRequired()])
    lme = FloatField('LME Price', validators=[InputRequired()])
    m_lme = FloatField('LME Markup', validators=[Optional()])
    sec = FloatField('SC', validators=[Optional()])
    tc = FloatField('TC', validators=[Optional()])
    exchange = FloatField('Exchange Rate', validators=[Optional()])
    transport_tag = FloatField('Transport/Tag Per Kg', validators=[Optional()])
    rma_default = FloatField('RMA DEFAULT', validators=[Optional()])
    inkomane_default = FloatField('Inkomane DEFAULT ', validators=[Optional()])
    advance_payment_ids = SelectMultipleField('Use supplier advances', choices=[], coerce=int, validators=[Optional()])
    submit = SubmitField('Add Cassiterite Stock')




class RecordCassiteriteOutputForm(FlaskForm):
    """Form to record cassiterite output"""
    date = DateField('Date', validators=[DataRequired()], format='%Y-%m-%d')
    stock_id = SelectField('Stock (Voucher)', coerce=int, validators=[DataRequired()])
    output_kg = FloatField('Output (kg)', validators=[InputRequired()])
    customer = StringField('Customer', validators=[Optional(), Length(min=2, max=100)])
    output_amount = FloatField('Output Amount', validators=[Optional()])
    amount_paid = FloatField('Cash paid', validators=[Optional()])
    currency = SelectField(
        'Currency',
        choices=[('RWF', 'RWF'), ('USD', 'USD')],
        validators=[DataRequired()],
        default='RWF'
    )
    exchange_rate = FloatField(
        'Exchange Rate (RWF per currency unit)',
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1.0
    )
    payment_stage = SelectField(
        'Payment Stage',
        choices=[('advance', 'Advance'), ('final_settlement', 'Final Settlement'), ('full_settlement', 'Full Settlement')],
        validators=[DataRequired()],
        default='full_settlement'
    )
    note = TextAreaField('Note', validators=[Optional()])
    submit = SubmitField('Record Output')




class RecordCassiteritePaymentForm(FlaskForm):
    """Form to record customer payment (uses dropdown of owing customers)."""
    customer = SelectField('Customer', validators=[DataRequired()])
    payment_amount = FloatField('Payment Amount', validators=[InputRequired()])
    currency = SelectField(
        'Currency',
        choices=[('RWF', 'RWF'), ('USD', 'USD')],
        validators=[DataRequired()],
        default='RWF'
    )
    exchange_rate = FloatField(
        'Exchange Rate (RWF per currency unit)',
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1.0
    )
    submit = SubmitField('Record Payment')


class OptimizeCassiteriteForm(FlaskForm):
    """Form to optimize cassiterite stocks by target quality"""
    target_moyenne = FloatField("Moyenne (quality percentage) that you want", validators=[Optional()])
    submit = SubmitField('Filter Stocks')


class CassiteriteSupplierPaymentForm(FlaskForm):
    """Form to record supplier payment for cassiterite"""
    payment_kind = SelectField(
        'Payment Type',
        choices=[('settlement', 'Settle Existing Supplier Debt'), ('advance', 'Pay Supplier Advance')],
        validators=[DataRequired()],
        default='settlement'
    )
    existing_supplier = SelectField('Existing Supplier (for advance)', choices=[], validators=[Optional()])
    new_supplier = StringField('Or New Supplier Name (for advance)', validators=[Optional(), Length(max=100)])
    stock_id = SelectField('Stock (Lot Number)', coerce=int, validators=[Optional()], validate_choice=False)
    amount = FloatField('Payment Amount', validators=[DataRequired()])
    currency = SelectField(
        'Currency',
        choices=[('RWF', 'RWF'), ('USD', 'USD')],
        validators=[DataRequired()],
        default='RWF'
    )
    exchange_rate = FloatField(
        'Exchange Rate (RWF per currency unit)',
        validators=[Optional(), NumberRange(min=0.0001)],
        default=None
    )
    paid_at = DateTimeLocalField(
        'Payment Date / Time',
        format='%Y-%m-%dT%H:%M',
        validators=[Optional()],
    )
    method = SelectField('Payment Method', 
                        choices=[('cash', 'Cash'), ('bank', 'Bank Transfer'), ('momo', 'Mobile Money')],
                        validators=[DataRequired()])
    reference = StringField('Receipt/Reference No', validators=[Optional(), Length(max=100)])
    note = TextAreaField('Notes', validators=[Optional(), Length(max=500)])
    change_reason = TextAreaField('Change / Delete Reason', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Record Supplier Payment')
