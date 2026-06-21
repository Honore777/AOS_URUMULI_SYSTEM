from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, InputRequired, NumberRange

class CassiteriteEditSoldStockForm(FlaskForm):
    voucher_no = StringField('Voucher No', render_kw={'readonly': True})
    supplier = StringField('Supplier', render_kw={'readonly': True})
    input_kg = DecimalField('Input (kg)', render_kw={'readonly': True})
    
    # Editable fields
    percentage = DecimalField('Percentage', validators=[InputRequired(), NumberRange(min=0)])
    u = DecimalField('U', validators=[InputRequired(), NumberRange(min=0)])
    u_price = DecimalField('U Price', validators=[InputRequired(), NumberRange(min=0)])
    exchange = DecimalField('Exchange', validators=[InputRequired(), NumberRange(min=0)])
    transport_tag = DecimalField('Transport (TAG)', validators=[InputRequired(), NumberRange(min=0)])
    
    # Per-unit defaults (editable)
    rma_default = DecimalField('RMA Default (per kg)', validators=[InputRequired(), NumberRange(min=0)])
    inkomane_default = DecimalField('Inkomane Default (per kg)', validators=[InputRequired(), NumberRange(min=0)])
    rra_3_percent_default = DecimalField('RRA 3% Default', validators=[InputRequired(), NumberRange(min=0)])
    
    # Cassiterite-specific editable fields
    lme = DecimalField('LME', validators=[InputRequired(), NumberRange(min=0)])
    m_lme = DecimalField('M LME', validators=[InputRequired(), NumberRange(min=0)])
    sec = DecimalField('SEC', validators=[InputRequired(), NumberRange(min=0)])
    tc = DecimalField('TC', validators=[InputRequired(), NumberRange(min=0)])
    
    # Calculated fields (read-only)
    amount = DecimalField('Amount', render_kw={'readonly': True})
    amount_with_taxes = DecimalField('Amount with Taxes', render_kw={'readonly': True})
    net_balance = DecimalField('Net Balance', render_kw={'readonly': True})
    
    reason = TextAreaField('Reason for Edit', validators=[DataRequired()])
    submit = SubmitField('Update Stock')
