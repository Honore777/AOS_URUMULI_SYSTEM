"""
Copper Output Form
For recording copper sales/outputs
"""
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SubmitField, DateField, SelectField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Optional, NumberRange


class CopperOutputForm(FlaskForm):
    """Form for recording copper output"""
    stock_id = SelectField('Select Stock', coerce=int, validators=[DataRequired()])
    date = DateField('Date', format='%Y-%m-%d', validators=[DataRequired()])
    customer = StringField('Customer Name', validators=[Optional()])
    output_kg = FloatField('Output (Kg)', validators=[InputRequired()])
    output_amount = FloatField('Sales Amount', validators=[Optional()])
    amount_paid = FloatField('Cash paid', validators=[Optional()])
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
    payment_stage = SelectField(
        'Payment Stage',
        choices=[('advance', 'Advance'), ('final_settlement', 'Final Settlement'), ('full_settlement', 'Full Settlement')],
        validators=[DataRequired()],
        default='full_settlement',
    )
    note = TextAreaField('Note', validators=[Optional()])
    submit = SubmitField('Record Output')
