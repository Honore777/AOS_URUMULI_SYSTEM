"""
Copper Stock Form
For adding new copper stock entries
"""
from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SubmitField, DateField, SelectMultipleField
from wtforms.validators import DataRequired, InputRequired, NumberRange, Optional


class CopperStockForm(FlaskForm):
    """Form for adding copper stock"""
    date = DateField('Date', validators=[DataRequired()])
    voucher_no = StringField('Voucher No', validators=[DataRequired()])
    supplier = StringField('Supplier', validators=[DataRequired()])
    input_kg = DecimalField('Input (kg)', validators=[InputRequired(), NumberRange(min=0)])
    percentage = DecimalField('Percentage', validators=[InputRequired(), NumberRange(min=0)])
    nb = DecimalField('NB', validators=[InputRequired(), NumberRange(min=0)])
    u_price = DecimalField('U Price', validators=[InputRequired(), NumberRange(min=0)])
    exchange = DecimalField('Exchange', validators=[InputRequired(), NumberRange(min=0)])
    transport_tag = DecimalField('Transport (TAG)', validators=[InputRequired(), NumberRange(min=0)])
    rma_default = DecimalField('RMA default', default=125, validators=[NumberRange(min=0)])
    rra_3_percent_default = DecimalField('RRA 3 Percent Default', default=5, validators=[NumberRange(min=0)])
    inkomane_default = DecimalField('Inkomane default', default=40, validators=[NumberRange(min=0)])
    advance_payment_ids = SelectMultipleField('Use supplier advances', choices=[], coerce=int, validators=[Optional()])
    submit = SubmitField('Add Stock')
