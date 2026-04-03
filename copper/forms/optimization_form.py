"""
Copper Optimization Form
For stock optimization based on moyenne
"""
from flask_wtf import FlaskForm
from wtforms import FloatField, SubmitField
from wtforms.validators import Optional


class CopperOptimizationForm(FlaskForm):
    """Form for copper stock optimization"""
    target_moyenne = FloatField("Moyenne (percentage) that you want", validators=[Optional()])
    target_moyenne_nb = FloatField("Moyenne_nb (nobelium) that you want", validators=[Optional()])
    target_total_quantity = FloatField("Target total quantity (kg)", validators=[Optional()])
    submit = SubmitField('Optimize stocks')
