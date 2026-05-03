import logging

from flask import jsonify

from config import db
from core.auth import role_required

from sqlalchemy import func

from . import core_bp

logger = logging.getLogger(__name__)


@core_bp.route("/api/remaining_stock")
@role_required("accountant", "boss", "store_keeper", "admin")
def api_remaining_stock():
    """API endpoint to get remaining stock in kilograms for both minerals."""
    try:
        from copper.models import CopperStock
        from cassiterite.models import CassiteriteStock

        copper_remaining_kg = (
            db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0))
            .filter(CopperStock.is_deleted.is_(False))
            .scalar()
            or 0.0
        )
        cass_remaining_kg = (
            db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0))
            .filter(CassiteriteStock.is_deleted.is_(False))
            .scalar()
            or 0.0
        )
        total_remaining = float(copper_remaining_kg or 0.0) + float(cass_remaining_kg or 0.0)

        return jsonify({
            'copper_remaining_kg': float(copper_remaining_kg or 0.0),
            'cassiterite_remaining_kg': float(cass_remaining_kg or 0.0),
            'total_remaining_kg': float(total_remaining or 0.0),
        })
    except Exception as e:
        logger.exception("api_remaining_stock failed")
        return jsonify({'error': str(e)}), 500
