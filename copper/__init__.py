"""
Copper Module
Manages all copper-related functionality: stocks, outputs, debts, optimization, and supplier payments
"""
from flask import Blueprint
from flask_login import login_required
from core.auth import role_required

# Create blueprint
copper_bp = Blueprint(
    'copper',
    __name__,
    url_prefix='/copper'
)

@copper_bp.before_request
@login_required
@role_required('accountant', 'cashier', 'boss', 'admin')
def copper_role_protect():
    pass
# Import routes module after blueprint is defined
from . import routes
