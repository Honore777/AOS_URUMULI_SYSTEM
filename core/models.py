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


class User(db.Model):
        """System user

        This is a simple user model with a ROLE column so we can distinguish
        between:
        - accountant
        - store_keeper
        - boss

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

        # Role will be one of: 'accountant', 'store_keeper', 'boss'
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

        user = db.relationship("User", backref="notifications", lazy=True)


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

