import logging

from flask import jsonify, request
from flask_login import current_user

from config import db
from core.auth import role_required

from sqlalchemy import func

from . import core_bp

logger = logging.getLogger(__name__)


@core_bp.route('/api/push/register', methods=['POST'])
@role_required('negotiator', 'cashier', 'accountant', 'boss', 'store_keeper', 'admin')
def api_register_push_token():
    try:
        payload = request.get_json(silent=True) or {}
        token = (payload.get('token') or '').strip()
        user_agent = (payload.get('user_agent') or '').strip()[:255] or None
        if not token:
            return jsonify({'error': 'Missing token'}), 400

        from core.models import PushToken
        uid = int(getattr(current_user, 'id', 0) or 0)
        if not uid:
            return jsonify({'error': 'Not authenticated'}), 401

        row = PushToken.query.filter_by(token=token).first()
        if row:
            row.user_id = uid
            row.user_agent = user_agent
            row.last_seen_at = func.now()
            db.session.add(row)
        else:
            row = PushToken(
                user_id=uid,
                token=token,
                user_agent=user_agent,
                created_at=func.now(),
                last_seen_at=func.now(),
            )
            db.session.add(row)

        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        logger.exception('api_register_push_token failed')
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': str(e)}), 500


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
