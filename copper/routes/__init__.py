"""
Copper routes module
All routes use traditional Flask Blueprint decorators
"""
from . import stock_routes
from . import output_routes
from . import debt_routes
from . import optimization_routes
from . import payment_routes
from . import solver_debug
