"""
Cassiterite Blueprint
"""
from flask import Blueprint

cassiterite_bp = Blueprint(
    'cassiterite',
    __name__,
    url_prefix='/cassiterite',
    template_folder='../../templates/cassiterite',
    static_folder='../../static'
)

from flask_login import login_required

from core.auth import role_required


@cassiterite_bp.before_request
@login_required
@role_required('accountant', 'cashier', 'boss', 'admin')
def cassiterite_role_protect():
    pass

# Import route modules
from . import stock_routes, output_routes, debt_routes, supplier_routes

__all__ = ['cassiterite_bp']
