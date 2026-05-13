"""Core/shared models




</html></body>    </div>        </div>            <a href="{{ url_for('entry_point') }}" class="text-emerald-600 hover:text-emerald-800">&larr; Back to main entry</a>        <div class="mt-6 text-sm text-slate-500">        {% endif %}            <p class="text-sm text-slate-500">No users found.</p>        {% else %}            </div>                </table>                    </tbody>                        {% endfor %}                            </tr>                                </td>                                    </div>                                        </form>                                            </button>                                                Delete                                            >                                                class="inline-flex items-center rounded border border-red-300 px-2 py-1 font-semibold text-red-700 hover:bg-red-50"                                                type="submit"                                            <button                                        >                                            onsubmit="return confirm('Are you sure you want to permanently delete this user?');"                                            action="{{ url_for('core.admin_delete_user', user_id=u.id) }}"                                            method="post"                                        <form                                        </form>                                            </button>                                                {% if u.is_active %}Deactivate{% else %}Activate{% endif %}                                            >                                                class="inline-flex items-center rounded border border-amber-300 px-2 py-1 font-semibold text-amber-700 hover:bg-amber-50"                                                type="submit"                                            <button                                        <form method="post" action="{{ url_for('core.admin_toggle_user_active', user_id=u.id) }}">                                        </a>                                            Edit                                        >                                            class="inline-flex items-center rounded border border-slate-300 px-2 py-1 font-semibold text-slate-700 hover:bg-slate-50"                                            href="{{ url_for('core.admin_edit_user', user_id=u.id) }}"                                        <a                                    <div class="flex flex-wrap gap-2 text-xs">                                <td class="px-3 py-2">                                </td>                                    {{ u.created_at.strftime('%Y-%m-%d %H:%M') if u.created_at else '-' }}                                <td class="px-3 py-2 text-xs text-slate-500">                                </td>                                    {% endif %}                                        <span class="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">Inactive</span>                                    {% else %}                                        <span class="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700">Active</span>                                    {% if u.is_active %}                                <td class="px-3 py-2">                                <td class="px-3 py-2 uppercase text-xs font-semibold text-slate-700">{{ u.role }}</td>                                <td class="px-3 py-2 text-slate-700">{{ u.email or '-' }}</td>                                <td class="px-3 py-2 text-slate-900 font-semibold">{{ u.username }}</td>                                <td class="px-3 py-2 text-slate-700">{{ u.id }}</td>                            <tr class="hover:bg-slate-50">                        {% for u in users %}                    <tbody class="divide-y divide-slate-200">                    </thead>                        </tr>                            <th class="px-3 py-2 text-left">Actions</th>                            <th class="px-3 py-2 text-left">Created</th>                            <th class="px-3 py-2 text-left">Active</th>                            <th class="px-3 py-2 text-left">Role</th>                            <th class="px-3 py-2 text-left">Email</th>                            <th class="px-3 py-2 text-left">Username</th>                            <th class="px-3 py-2 text-left">ID</th>                        <tr>                    <thead class="bg-slate-800 text-white">                <table class="min-w-full text-sm">            <div class="overflow-x-auto rounded-lg border border-slate-200">        {% if users %}        {% endwith %}            {% endif %}                </div>                    {% endfor %}                        </div>                            {{ message }}                                    {% else %}bg-slate-50 border-slate-200 text-slate-800{% endif %}">                                    {% elif category == 'danger' %}bg-red-50 border-red-200 text-red-800                                    {% elif category == 'warning' %}bg-amber-50 border-amber-200 text-amber-800                                    {% if category == 'success' %}bg-emerald-50 border-emerald-200 text-emerald-800                        <div class="text-sm px-3 py-2 rounded border                    {% for category, message in messages %}                <div class="mb-4 space-y-2">            {% if messages %}        {% with messages = get_flashed_messages(with_categories=true) %}        </div>            </a>                + New User            >                class="ml-auto inline-flex items-center rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700"                href="{{ url_for('core.admin_create_user') }}"            <a            <h1 class="text-2xl font-extrabold text-slate-900">User Management</h1>        <div class="flex items-center mb-6">    <div class="max-w-6xl mx-auto bg-white rounded-xl shadow-2xl p-8"><body class="bg-slate-900 min-h-screen p-6"></head>    <script src="https://cdn.tailwindcss.com"></script>    <title>Admin - Users</title>    <meta name="viewport" content="width=device-width, initial-scale=1.0" />    <meta charset="UTF-8" /><head>These models are NOT specific to copper or cassiterite.
They represent:
- Application users and their roles (accountant, store_keeper, boss)
- In-app notifications
- Bulk output plans coming from the optimization system
- Payment reviews for the boss

Keeping them in this "core" package avoids duplication between
different minerals/modules.
"""

from datetime import datetime
import enum

from config import db
from werkzeug.security import generate_password_hash, check_password_hash
import logging
import os
from sqlalchemy import func
from sqlalchemy.orm import backref


class User(db.Model):
        """System user

        This is a simple user model with a ROLE column so we can distinguish
        between:
        - accountant
        - cashier
        - negotiator
        - store_keeper
        - boss
        - admin

        NOTE: This model is designed to work nicely with Flask-Login later.
        For now, it just provides helpers to hash/check passwords.
        """

        __tablename__ = "user"

        id = db.Column(db.Integer, primary_key=True)

        # Login identity
        username = db.Column(db.String(64), unique=True, nullable=False)
        email = db.Column(db.String(120), unique=True, nullable=True)

        # Hashed password (NEVER store raw passwords)
        password_hash = db.Column(db.String(255), nullable=False)

        # Role is currently stored as a string (examples above).
        role = db.Column(db.String(20), nullable=False, default="accountant")

        # Extra flags / metadata
        is_active = db.Column(db.Boolean, default=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        # ------------------------------------------------------------------
        # Helper methods
        # ------------------------------------------------------------------
        def set_password(self, raw_password: str) -> None:
                """Hash and store the user's password.

                We use Werkzeug's helpers so we don't manually deal with salts
                or hashing algorithms.
                """

                self.password_hash = generate_password_hash(raw_password)

        def check_password(self, raw_password: str) -> bool:
                """Return True if the given password matches the stored hash."""

                return check_password_hash(self.password_hash, raw_password)

        # If you integrate Flask-Login, this is the ID it will use.
        def get_id(self) -> str:  # pragma: no cover - very simple helper
                return str(self.id)

        # Flask-Login compatibility helpers -------------------------------

        @property
        def is_authenticated(self) -> bool:  # pragma: no cover - trivial
                """Flask-Login uses this to know if the user is logged in."""

                return True

        @property
        def is_anonymous(self) -> bool:  # pragma: no cover - trivial
                """Our User objects are never anonymous."""

                return False


class Notification(db.Model):
        """In-app notification for a single user.

        This is the basis for the notification system (the little alerts
        inside the app). We will create one Notification row each time
        something important happens for a user, for example:

        - New bulk optimization plan was created (store_keeper should see it).
        - A payment was executed (boss should review it).
        - A bulk plan was executed (accountant/store_keeper see the result).
        """

        __tablename__ = "notification"

        id = db.Column(db.Integer, primary_key=True)

        # Which user this notification belongs to
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

        # When it was created and (optionally) when the user has seen it
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        read_at = db.Column(db.DateTime, nullable=True)

        # Short machine-readable type, for example:
        # 'OUTPUT_CREATED', 'BULK_PLAN_CREATED', 'BULK_PLAN_EXECUTED',
        # 'PAYMENT_EXECUTED', 'PAYMENT_REVIEWED', ...
        type = db.Column(db.String(50), nullable=False)

        # Human-readable text that we can show directly in the UI
        message = db.Column(db.String(255), nullable=False)

        # Optional: what this notification is about (for deep-linking)
        # Example: related_type='bulk_plan', related_id=<BulkOutputPlan.id>
        related_type = db.Column(db.String(50), nullable=True)
        related_id = db.Column(db.Integer, nullable=True)

        user = db.relationship(
            "User",
            backref=backref("notifications", cascade="all, delete-orphan"),
            lazy=True,
        )


class PushToken(db.Model):
        __tablename__ = 'push_token'

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
        token = db.Column(db.Text, nullable=False, unique=True)
        user_agent = db.Column(db.String(255), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        user = db.relationship('User', backref=backref('push_tokens', cascade='all, delete-orphan'), lazy=True)


_firebase_app = None


def _get_firebase_app():
        global _firebase_app
        if _firebase_app is not None:
                return _firebase_app

        logger = logging.getLogger(__name__)
        try:
                import firebase_admin
                from firebase_admin import credentials
        except Exception:
                logger.debug('firebase_admin not installed; push notifications disabled')
                _firebase_app = False
                return _firebase_app

        project_id = os.environ.get('FIREBASE_PROJECT_ID')
        client_email = os.environ.get('FIREBASE_CLIENT_EMAIL')
        private_key = os.environ.get('FIREBASE_PRIVATE_KEY')
        if not project_id or not client_email or not private_key:
                logger.debug('Firebase env vars not configured; push notifications disabled')
                _firebase_app = False
                return _firebase_app

        try:
                private_key = private_key.replace('\\n', '\n')
                cred = credentials.Certificate({
                        'type': 'service_account',
                        'project_id': project_id,
                        'client_email': client_email,
                        'private_key': private_key,
                        'token_uri': 'https://oauth2.googleapis.com/token',
                })
                _firebase_app = firebase_admin.initialize_app(cred)
                return _firebase_app
        except Exception:
                logger.exception('Failed to initialize Firebase Admin; push notifications disabled')
                _firebase_app = False
                return _firebase_app


def _send_push_to_user(user_id: int, title: str, body: str, data: dict | None = None) -> None:
        logger = logging.getLogger(__name__)
        app = _get_firebase_app()
        if not app:
                return

        try:
                from firebase_admin import messaging
        except Exception:
                return

        try:
                tokens = [t.token for t in PushToken.query.filter_by(user_id=user_id).limit(10).all()]
        except Exception:
                logger.exception('Failed to load push tokens')
                return

        if not tokens:
                return

        msg_data = {str(k): str(v) for (k, v) in (data or {}).items()}
        message = messaging.MulticastMessage(
                notification=messaging.Notification(title=title, body=body),
                data=msg_data,
                tokens=tokens,
        )

        try:
                resp = messaging.send_multicast(message)
                if resp.failure_count:
                        for idx, r in enumerate(resp.responses):
                                if not r.success:
                                        logger.debug('Push failed for token index=%s err=%s', idx, getattr(r, 'exception', None))
        except Exception:
                logger.exception('Failed to send push notification')


def create_notification(user_id: int,
                                                type_: str,
                                                message: str,
                                                related_type: str | None = None,
                                                related_id: int | None = None) -> None:
        """Small helper to create and stage a Notification.

        We call this from routes/services whenever something important
        happens (bulk plan created, payment executed, etc.).

        SIGNIFICANCE:
        - Keeps route code clean: instead of repeating 5 lines everywhere
            (set fields, db.session.add(...)), we centralize that logic here.
        - If we later change the Notification structure (for example,
            adding extra fields), we only need to update this helper.

        NOTE: This function only *adds* the notification to the session.
        The surrounding route/view is still responsible for calling
        db.session.commit().
        """

        notif = Notification(
                user_id=user_id,
                type=type_,
                message=message,
                related_type=related_type,
                related_id=related_id,
        )
        db.session.add(notif)

        try:
                link = ''
                if related_type and related_id:
                        link = f"{related_type}:{related_id}"
                _send_push_to_user(
                        user_id=int(user_id),
                        title='Urumuli Smart System',
                        body=str(message),
                        data={'type': str(type_), 'related': link},
                )
        except Exception:
                logging.getLogger(__name__).debug('Push send failed (ignored)', exc_info=True)


def fetch_user_notifications(user_id: int, unread_limit: int = 20, read_limit: int = 10):
        """Helper to fetch notifications for a user with step-by-step logging.

        Returns a tuple: (notifications_list, unread_count)
        """
        logger = logging.getLogger(__name__)
        try:
                logger.debug("fetch_user_notifications: start user_id=%s unread_limit=%s read_limit=%s", user_id, unread_limit, read_limit)

                # Best-effort: clear any previously aborted transaction so we can run read-only queries.
                try:
                        db.session.rollback()
                        logger.debug("fetch_user_notifications: pre-rollback executed")
                except Exception:
                        logger.exception("fetch_user_notifications: pre-rollback failed")

                # Prepare unread query
                try:
                        unread_q = db.session.query(Notification).filter(Notification.user_id == user_id, Notification.read_at == None).order_by(Notification.created_at.desc()).limit(unread_limit)
                        logger.debug("fetch_user_notifications: unread_q prepared: %s", getattr(unread_q, 'statement', repr(unread_q)))
                        unread = unread_q.all()
                        logger.debug("fetch_user_notifications: unread fetched count=%s", len(unread) if unread is not None else 0)
                except Exception:
                        logger.exception("fetch_user_notifications: unread query failed")
                        try:
                                db.session.rollback()
                        except Exception:
                                logger.exception("fetch_user_notifications: rollback after unread query failed")
                        unread = []

                # Prepare read query
                try:
                        read_q = db.session.query(Notification).filter(Notification.user_id == user_id, Notification.read_at != None).order_by(Notification.created_at.desc()).limit(read_limit)
                        logger.debug("fetch_user_notifications: read_q prepared: %s", getattr(read_q, 'statement', repr(read_q)))
                        read = read_q.all()
                        logger.debug("fetch_user_notifications: read fetched count=%s", len(read) if read is not None else 0)
                except Exception:
                        logger.exception("fetch_user_notifications: read query failed")
                        try:
                                db.session.rollback()
                        except Exception:
                                logger.exception("fetch_user_notifications: rollback after read query failed")
                        read = []

                # Compute unread count best-effort (may be approximated by len(unread))
                try:
                        unread_count = db.session.query(func.coalesce(func.count(Notification.id), 0)).filter(Notification.user_id == user_id, Notification.read_at == None).scalar()
                        unread_count = int(unread_count or 0)
                        logger.debug("fetch_user_notifications: unread_count=%s for user_id=%s", unread_count, user_id)
                except Exception:
                        logger.exception("fetch_user_notifications: unread_count query failed; falling back to len(unread)")
                        try:
                                db.session.rollback()
                        except Exception:
                                logger.exception("fetch_user_notifications: rollback after unread_count failed")
                        unread_count = len(unread) if unread is not None else 0

                notifications = (unread or []) + (read or [])
                return notifications, int(unread_count or 0)
        except Exception as e:
                logger.exception("fetch_user_notifications: unexpected failure: %s", e)
                try:
                        db.session.rollback()
                except Exception:
                        logger.exception("fetch_user_notifications: rollback after unexpected failure failed")
                return [], 0


class BulkPlanStatus(enum.Enum):
        """Possible states for a bulk optimization plan.

        For now we keep it simple:
        - SENT_TO_STORE: plan was created and store_keeper should see it.
        - EXECUTED: the plan has actually been turned into Output records
            and stocks were reduced.

        Later, if you want more control, you can add states like
        'PENDING_EXECUTION', 'CANCELLED', etc.
        """

        SENT_TO_STORE = "SENT_TO_STORE"
        STOCK_CONFIRMED = "STOCK_CONFIRMED"
        EXECUTED = "EXECUTED"


class BulkOutputPlan(db.Model):
        """Snapshot of an optimized bulk output plan.

        This is where we STORE the exact optimal table that comes from
        the optimization system when the accountant clicks "Confirm".

        IMPORTANT:
        - This does NOT replace your existing Output logic. We will first
            use it as an AUDIT record and for store_keeper visibility.
        - Later, if you want, we can make execution happen from this plan
            (instead of directly inside the optimization route).
        """

        __tablename__ = "bulk_output_plan"

        id = db.Column(db.Integer, primary_key=True)

        # Which mineral this plan is for ('copper' or 'cassiterite')
        mineral_type = db.Column(db.String(20), nullable=False)

        # Who created the plan (usually the accountant)
        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)

        # Current status of the plan (string copy of BulkPlanStatus value)
        status = db.Column(db.String(20), nullable=False,
                                             default=BulkPlanStatus.SENT_TO_STORE.value)

        # Who actually executed the plan (accountant OR store_keeper)
        executed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        executed_at = db.Column(db.DateTime, nullable=True)

        # Optional customer / batch info (helps the store keeper understand
        # what this plan is for)
        customer = db.Column(db.String(100), nullable=True)
        batch_id = db.Column(db.String(100), nullable=True)
        note = db.Column(db.Text)
        
        # Total agreed/expected amount for the entire batch (for debt tracking)
        # This is the total price the customer agreed to pay for the batch
        # Debt = total_expected_amount - total_payments_received
        total_expected_amount = db.Column(db.Float, nullable=True, default=0)

        # The optimal table from the optimization step as JSON.
        # Typical structure (Python side before JSON):
        # [
        #   {"stock_id": 1, "voucher_no": "V123", "supplier": "ABC",
        #    "planned_output_kg": 500.0},
        #   {"stock_id": 4, "voucher_no": "V130", "supplier": "XYZ",
        #    "planned_output_kg": 300.0},
        # ]
        #
        # This way the store keeper (and boss) can see exactly which
        # stock lines were chosen and for how many kilograms.
        plan_json = db.Column(db.JSON, nullable=False)


class CustomerReceiptType(enum.Enum):
        """Lifecycle stage for customer receipts against an executed batch."""

        ADVANCE = "ADVANCE"
        INSTALLMENT = "INSTALLMENT"
        FINAL_SETTLEMENT = "FINAL_SETTLEMENT"


class CustomerReceiptChannel(enum.Enum):
        """Where the customer paid money into."""

        CASH = "CASH"
        BANK = "BANK"


class CustomerReceipt(db.Model):
        """Immutable customer receipt event (advance/installment/final settlement)."""

        __tablename__ = "customer_receipt"

        id = db.Column(db.Integer, primary_key=True)

        # Cross-module references
        mineral_type = db.Column(db.String(20), nullable=False, index=True)
        batch_id = db.Column(db.String(100), nullable=False, index=True)
        customer = db.Column(db.String(100), nullable=False, index=True)
        bulk_plan_id = db.Column(db.Integer, db.ForeignKey("bulk_output_plan.id"), nullable=True, index=True)

        # Receipt timing and classification
        received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        receipt_type = db.Column(db.String(30), nullable=False, default=CustomerReceiptType.ADVANCE.value, index=True)
        payment_channel = db.Column(db.String(20), nullable=False, default=CustomerReceiptChannel.CASH.value, index=True)

        # Entered and normalized amounts
        amount_input = db.Column(db.Float, nullable=False, default=0)
        currency = db.Column(db.String(10), nullable=False, default="RWF", index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        amount_rwf = db.Column(db.Float, nullable=False, default=0)

        # Audit fields
        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        note = db.Column(db.Text, nullable=True)

        # Optional proof for audit (photo path under /static/...)
        proof_image_path = db.Column(db.String(255), nullable=True)
        proof_uploaded_at = db.Column(db.DateTime, nullable=True)

        plan = db.relationship("BulkOutputPlan", backref="customer_receipts", lazy=True)
        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)
        # Cash collection tracking (set when cashier confirms physical cash)
        is_collected = db.Column(db.Boolean, nullable=False, default=False, index=True)
        collected_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        collected_at = db.Column(db.DateTime, nullable=True)
        cash_account_id = db.Column(db.Integer, db.ForeignKey('cash_account.id'), nullable=True)

        is_handed_over = db.Column(db.Boolean, nullable=False, default=False, index=True)
        handed_over_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        handed_over_at = db.Column(db.DateTime, nullable=True)

        collected_by = db.relationship('User', foreign_keys=[collected_by_id], lazy=True)
        handed_over_by = db.relationship('User', foreign_keys=[handed_over_by_id], lazy=True)


class StockChangeLog(db.Model):
        """Audit trail for stock changes.

        This does not replace business logic; it provides a professional,
        queryable history for boss/auditors whenever a stock row is edited or
        soft-deleted.
        """

        __tablename__ = "stock_change_log"

        id = db.Column(db.Integer, primary_key=True)
        mineral_type = db.Column(db.String(20), nullable=False, index=True)
        stock_id = db.Column(db.Integer, nullable=False, index=True)
        action = db.Column(db.String(20), nullable=False, index=True)  # EDIT | DELETE

        reason = db.Column(db.Text, nullable=True)
        before_json = db.Column(db.JSON, nullable=True)
        after_json = db.Column(db.JSON, nullable=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)

        # Audit fields for edits to the reason field itself
        original_reason = db.Column(db.Text, nullable=True)
        reason_edited_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        reason_edited_at = db.Column(db.DateTime, nullable=True)
        reason_edit_reason = db.Column(db.Text, nullable=True)

        reason_edited_by = db.relationship("User", foreign_keys=[reason_edited_by_id], lazy=True)


class StockAggregate(db.Model):
        """Lightweight single-row aggregate for stock system state.

        This table stores running totals so the application can avoid
        expensive full-table updates or repeated SUM(...) queries.

        We keep a `mineral_type` so we can track aggregates for different
        minerals (e.g. 'copper', 'cassiterite') in the same table.
        """

        __tablename__ = "stock_aggregate"

        id = db.Column(db.Integer, primary_key=True)
        mineral_type = db.Column(db.String(32), nullable=False, unique=True)

        total_quantity = db.Column(db.Float, nullable=False, default=0.0)
        total_weighted_percent = db.Column(db.Float, nullable=False, default=0.0)
        total_t_unity = db.Column(db.Float, nullable=False, default=0.0)

        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        def __repr__(self) -> str:
                return f"<StockAggregate {self.mineral_type}: qty={self.total_quantity} wp={self.total_weighted_percent}>"

        @staticmethod
        def get(mineral_type: str, create: bool = False):
                agg = db.session.query(StockAggregate).filter_by(mineral_type=mineral_type).first()
                if not agg and create:
                        agg = StockAggregate(mineral_type=mineral_type, total_quantity=0.0, total_weighted_percent=0.0, total_t_unity=0.0)
                        db.session.add(agg)
                        db.session.flush()
                return agg


class PaymentReviewStatus(enum.Enum):
        """Review status for a payment executed by the accountant.

        The accountant is allowed to pay immediately, but the boss should
        be able to review what was done afterwards.

        - PENDING_REVIEW: default, waiting for boss decision
        - APPROVED: boss accepted that payment
        - REJECTED: boss marked it as problematic (with a comment)
        """

        PENDING_REVIEW = "PENDING_REVIEW"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"


class PaymentReview(db.Model):
        """Record of a payment that the boss should review.

        This is linked to the REAL payment/output in your existing
        payment logic. Whenever an accountant records a payment to
        a customer, we will create a PaymentReview row so that the
        boss can later mark it as APPROVED or REJECTED.
        """

        __tablename__ = "payment_review"

        id = db.Column(db.Integer, primary_key=True)

        # Mineral this payment is about ('copper' or 'cassiterite')
        # For worker payments this may be unknown, make nullable.
        mineral_type = db.Column(db.String(20), nullable=True)

        # Type of payment: 'supplier', 'worker', 'customer'.
        # 'supplier' = Kwishyura Umutangwa
        # 'worker' = Kwishyura Umukozi
        # 'customer' = Kwishyurwa n’Umukiriya
        type = db.Column(db.String(32), nullable=True)

        # Basic payment info (mirrors your existing payment fields)
        customer = db.Column(db.String(100), nullable=False)
        amount = db.Column(db.Float, nullable=False)
        currency = db.Column(db.String(10), default="RWF")

        # Optional link to the actual payment/receipt/output record.
        # We keep it as a simple integer for now so you can decide later
        # which table to reference (copper or cassiterite payments).
        payment_id = db.Column(db.Integer, nullable=True)

        # Who created this payment (the accountant who executed it)
        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        # Current review status (string copy of PaymentReviewStatus value)
        status = db.Column(db.String(20), nullable=False,
                                             default=PaymentReviewStatus.PENDING_REVIEW.value)

        # Boss decision and optional comment
        reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        reviewed_at = db.Column(db.DateTime, nullable=True)
        boss_comment = db.Column(db.Text)
        request_payload = db.Column(db.Text, nullable=True)

        disbursement_status = db.Column(
                db.String(20),
                nullable=False,
                default="NOT_DISBURSED",
                index=True,
        )
        disbursed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        disbursed_at = db.Column(db.DateTime, nullable=True)
        cash_account_id = db.Column(db.Integer, db.ForeignKey('cash_account.id'), nullable=True)
        cash_transaction_id = db.Column(db.Integer, nullable=True)


class SupplierTransactionType(enum.Enum):
        """Financial movement type for supplier transactions."""

        ADVANCE = "ADVANCE"
        STOCK_PAYMENT = "STOCK_PAYMENT"
        ADJUSTMENT = "ADJUSTMENT"
        REFUND = "REFUND"


class TransactionApprovalStatus(enum.Enum):
        """Approval lifecycle for requests that require boss decision."""

        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"


class TransactionDisbursementStatus(enum.Enum):
        """Cash/bank execution lifecycle after approval."""

        NOT_DISBURSED = "NOT_DISBURSED"
        DISBURSED = "DISBURSED"
        CANCELLED = "CANCELLED"


class ExpenseCategory(enum.Enum):
        """Generalized internal expense categories.

        This replaces a worker-only payment mindset and allows recording
        items like labor, stationery, utilities, and other internal costs.
        """

        LABOR = "LABOR"
        STATIONERY = "STATIONERY"
        UTILITIES = "UTILITIES"
        OTHER = "OTHER"


class ExpenseTransaction(db.Model):
        """Generic internal expense transaction.

        Replaces worker-only payments with a broader model for labor and
        non-labor business expenses while preserving approval/disbursement flow.
        """

        __tablename__ = "expense_transaction"

        id = db.Column(db.Integer, primary_key=True)

        category = db.Column(
                db.String(20),
                nullable=False,
                default=ExpenseCategory.OTHER.value,
                index=True,
        )

        # Compatibility field used by existing worker-payment routes.
        worker_name = db.Column(db.String(120), nullable=True, index=True)
        # Generic payee name for non-worker expenses.
        payee_name = db.Column(db.String(120), nullable=True, index=True)
        description = db.Column(db.Text, nullable=True)
        amount = db.Column(db.Float, nullable=False)
        currency = db.Column(db.String(10), nullable=False, default="RWF")
        method = db.Column(db.String(20), nullable=True)  # CASH | BANK | MOMO
        reference = db.Column(db.String(100), nullable=True)
        note = db.Column(db.Text, nullable=True)
        paid_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        # Optional: used for filtered reporting by mineral in transition phase.
        mineral_type = db.Column(db.String(20), nullable=True, index=True)

        approval_status = db.Column(
                db.String(20),
                nullable=False,
                default=TransactionApprovalStatus.PENDING.value,
                index=True,
        )
        approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        approved_at = db.Column(db.DateTime, nullable=True)

        disbursement_status = db.Column(
                db.String(20),
                nullable=False,
                default=TransactionDisbursementStatus.NOT_DISBURSED.value,
                index=True,
        )
        disbursed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        disbursed_at = db.Column(db.DateTime, nullable=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
        deleted_at = db.Column(db.DateTime, nullable=True)
        deleted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        delete_reason = db.Column(db.Text, nullable=True)

        approved_by = db.relationship("User", foreign_keys=[approved_by_id], lazy=True)
        disbursed_by = db.relationship("User", foreign_keys=[disbursed_by_id], lazy=True)
        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)
        deleted_by = db.relationship("User", foreign_keys=[deleted_by_id], lazy=True)


class CashAccount(db.Model):
        """Represents a named cash drawer or bank-like cash account used by cashiers.

        Minimal fields so the Cashier can record `cash_in` and `cash_out` transactions
        and reconcile balances per account.
        """

        __tablename__ = "cash_account"

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False, unique=True)
        currency = db.Column(db.String(10), nullable=False, default="RWF", index=True)
        opening_balance = db.Column(db.Float, nullable=False, default=0.0)
        current_balance = db.Column(db.Float, nullable=False, default=0.0)
        create_reason = db.Column(db.Text, nullable=True)
        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CashTransaction(db.Model):
        """Immutable record of a cash movement recorded by a cashier.

        direction: 'IN' for cash in, 'OUT' for cash out.
        """

        __tablename__ = "cash_transaction"

        id = db.Column(db.Integer, primary_key=True)
        account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"), nullable=False, index=True)
        amount = db.Column(db.Float, nullable=False)
        currency = db.Column(db.String(10), nullable=False, default="RWF", index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        amount_input = db.Column(db.Float, nullable=True)
        amount_rwf = db.Column(db.Float, nullable=True)
        direction = db.Column(db.String(4), nullable=False)  # IN | OUT
        reference = db.Column(db.String(140), nullable=True, index=True)
        note = db.Column(db.Text, nullable=True)
        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        account = db.relationship("CashAccount", backref="transactions", lazy=True)
        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)


class CashReconciliation(db.Model):
        __tablename__ = "cash_reconciliation"

        id = db.Column(db.Integer, primary_key=True)
        account_id = db.Column(db.Integer, db.ForeignKey("cash_account.id"), nullable=False, index=True)
        recon_date = db.Column(db.Date, nullable=False, index=True)

        expected_balance = db.Column(db.Float, nullable=False, default=0.0)
        counted_balance = db.Column(db.Float, nullable=False, default=0.0)
        variance = db.Column(db.Float, nullable=False, default=0.0, index=True)

        note = db.Column(db.Text, nullable=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
        deleted_at = db.Column(db.DateTime, nullable=True)
        deleted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        delete_reason = db.Column(db.Text, nullable=True)

        account = db.relationship("CashAccount", lazy=True)
        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)
        deleted_by = db.relationship("User", foreign_keys=[deleted_by_id], lazy=True)


class UnifiedSupplierAdvance(db.Model):
        __tablename__ = "unified_supplier_advance"

        id = db.Column(db.Integer, primary_key=True)
        supplier_name = db.Column(db.String(120), nullable=False, index=True)
        supplier_name_norm = db.Column(db.String(140), nullable=False, index=True)

        source_mineral_type = db.Column(db.String(20), nullable=True, index=True)
        source_payment_id = db.Column(db.Integer, nullable=True, index=True)

        input_amount = db.Column(db.Float, nullable=True)
        currency = db.Column(db.String(10), nullable=False, default="RWF", index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        amount_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)

        paid_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        method = db.Column(db.String(50), nullable=True)
        reference = db.Column(db.String(100), nullable=True)
        note = db.Column(db.Text, nullable=True)

        advance_remaining = db.Column(db.Float, nullable=False, default=0.0, index=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)
        deleted_at = db.Column(db.DateTime, nullable=True)
        deleted_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
        delete_reason = db.Column(db.Text, nullable=True)

        created_by = db.relationship("User", foreign_keys=[created_by_id], lazy=True)
        deleted_by = db.relationship("User", foreign_keys=[deleted_by_id], lazy=True)


class UnifiedSupplierAdvanceAllocation(db.Model):
        __tablename__ = "unified_supplier_advance_allocation"

        id = db.Column(db.Integer, primary_key=True)
        advance_id = db.Column(db.Integer, db.ForeignKey("unified_supplier_advance.id"), nullable=False, index=True)
        stock_mineral_type = db.Column(db.String(20), nullable=False, index=True)
        stock_id = db.Column(db.Integer, nullable=False, index=True)
        applied_amount = db.Column(db.Float, nullable=False, default=0.0)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

        advance = db.relationship(
                "UnifiedSupplierAdvance",
                backref=backref("allocations", cascade="all, delete-orphan"),
                lazy=True,
        )


class Loan(db.Model):
        __tablename__ = 'loan'

        id = db.Column(db.Integer, primary_key=True)
        lender_name = db.Column(db.String(140), nullable=False, index=True)
        lender_name_norm = db.Column(db.String(160), nullable=False, index=True)

        principal_input = db.Column(db.Float, nullable=False, default=0.0)
        currency = db.Column(db.String(10), nullable=False, default='RWF', index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        principal_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)

        status = db.Column(db.String(30), nullable=False, default='PENDING_APPROVAL', index=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        boss_approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        boss_approved_at = db.Column(db.DateTime, nullable=True)

        note = db.Column(db.Text, nullable=True)

        outstanding_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)
        disbursed_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)
        repaid_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)

        created_by = db.relationship('User', foreign_keys=[created_by_id], lazy=True)
        boss_approved_by = db.relationship('User', foreign_keys=[boss_approved_by_id], lazy=True)


class LoanLedgerEntry(db.Model):
        __tablename__ = 'loan_ledger_entry'

        id = db.Column(db.Integer, primary_key=True)
        loan_id = db.Column(db.Integer, db.ForeignKey('loan.id'), nullable=False, index=True)
        entry_type = db.Column(db.String(30), nullable=False, index=True)  # DISBURSEMENT | REPAYMENT

        amount_input = db.Column(db.Float, nullable=False, default=0.0)
        currency = db.Column(db.String(10), nullable=False, default='RWF', index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        amount_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)

        cash_account_id = db.Column(db.Integer, db.ForeignKey('cash_account.id'), nullable=True, index=True)
        cash_transaction_id = db.Column(db.Integer, db.ForeignKey('cash_transaction.id'), nullable=True, index=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        note = db.Column(db.Text, nullable=True)

        loan = db.relationship('Loan', backref=backref('entries', cascade='all, delete-orphan'), lazy=True)
        cash_account = db.relationship('CashAccount', lazy=True)
        cash_transaction = db.relationship('CashTransaction', lazy=True)
        created_by = db.relationship('User', foreign_keys=[created_by_id], lazy=True)


class CustomerUnearnedReceipt(db.Model):
        __tablename__ = 'customer_unearned_receipt'

        id = db.Column(db.Integer, primary_key=True)
        mineral_type = db.Column(db.String(20), nullable=True, index=True)
        customer = db.Column(db.String(100), nullable=False, index=True)

        received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        payment_channel = db.Column(db.String(20), nullable=False, default=CustomerReceiptChannel.CASH.value, index=True)

        amount_input = db.Column(db.Float, nullable=False, default=0.0)
        currency = db.Column(db.String(10), nullable=False, default='RWF', index=True)
        exchange_rate = db.Column(db.Float, nullable=False, default=1.0)
        amount_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)
        remaining_rwf = db.Column(db.Float, nullable=False, default=0.0, index=True)

        note = db.Column(db.Text, nullable=True)

        proof_image_path = db.Column(db.String(255), nullable=True)
        proof_uploaded_at = db.Column(db.DateTime, nullable=True)

        created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        is_collected = db.Column(db.Boolean, nullable=False, default=False, index=True)
        collected_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        collected_at = db.Column(db.DateTime, nullable=True)
        cash_account_id = db.Column(db.Integer, db.ForeignKey('cash_account.id'), nullable=True)

        is_handed_over = db.Column(db.Boolean, nullable=False, default=False, index=True)
        handed_over_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        handed_over_at = db.Column(db.DateTime, nullable=True)

        created_by = db.relationship('User', foreign_keys=[created_by_id], lazy=True)
        collected_by = db.relationship('User', foreign_keys=[collected_by_id], lazy=True)
        cash_account = db.relationship('CashAccount', lazy=True)

        handed_over_by = db.relationship('User', foreign_keys='CustomerUnearnedReceipt.handed_over_by_id', lazy=True)


class CustomerUnearnedAllocation(db.Model):
        __tablename__ = 'customer_unearned_allocation'

        id = db.Column(db.Integer, primary_key=True)
        unearned_id = db.Column(db.Integer, db.ForeignKey('customer_unearned_receipt.id'), nullable=False, index=True)
        stock_mineral_type = db.Column(db.String(20), nullable=False, index=True)
        batch_id = db.Column(db.String(100), nullable=False, index=True)
        applied_amount_rwf = db.Column(db.Float, nullable=False, default=0.0)
        created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        note = db.Column(db.Text, nullable=True)

        unearned = db.relationship('CustomerUnearnedReceipt', backref=backref('allocations', cascade='all, delete-orphan'), lazy=True)
        created_by = db.relationship('User', foreign_keys=[created_by_id], lazy=True)


