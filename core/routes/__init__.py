"""Core-level routes and blueprints.

This package defines blueprints that are NOT specific to a single
mineral (copper or cassiterite). For now we expose:
- Boss consolidated dashboard and payment approvals
- Store keeper dashboard
- Notification utilities (mark read / mark all read)
"""

from flask import Blueprint

# Single core blueprint for cross-cutting views
core_bp = Blueprint("core", __name__)

# Import route modules so their view functions are registered
from . import management  # noqa: F401
from . import cashier_routes  # noqa: F401
from . import inventory_api  # noqa: F401
