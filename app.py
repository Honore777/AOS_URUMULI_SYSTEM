"""Smart Account Manager - Mining Company System

Main Flask application factory. This file wires together:
- Flask app + database
- Blueprints for minerals and core management
- Authentication (login/logout) using Flask-Login
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import Config, db
import logging
import os
from logging.handlers import RotatingFileHandler
from utils import trace_time
from sqlalchemy import func, or_
from core.models import User

app = Flask(__name__)
app.config.from_object(Config)


KIGALI_TZ = ZoneInfo('Africa/Kigali')


@app.template_filter('kigali_datetime')
def kigali_datetime(value, fmt='%Y-%m-%d %H:%M'):
    if not value:
        return ''
    try:
        dt = value
        if getattr(dt, 'tzinfo', None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KIGALI_TZ).strftime(fmt)
    except Exception:
        try:
            return value.strftime(fmt)
        except Exception:
            return str(value)

# Initialize database
db.init_app(app)
migrate = Migrate(app, db)

# Configure application logging using values from Config
try:
    log_level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO'))
except Exception:
    log_level = logging.INFO

# Ensure logs directory exists
log_file = app.config.get('LOG_FILE', 'logs/app.log')
log_dir = os.path.dirname(log_file)
if log_dir and not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception as e:
        print(f"Error creating log directory {log_dir}: {e}")

formatter = logging.Formatter(app.config.get('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
handler = RotatingFileHandler(
    log_file,
    maxBytes=int(app.config.get('LOG_MAX_BYTES', 10485760)),
    backupCount=int(app.config.get('LOG_BACKUP_COUNT', 5)),
)
handler.setLevel(log_level)
handler.setFormatter(formatter)

# Attach handler to Flask's app logger and root logger
app.logger.setLevel(log_level)
if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
    app.logger.addHandler(handler)
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
if not any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
    root_logger.addHandler(handler)

# Optionally enable SQL echoing for profiling
if app.config.get('SQLALCHEMY_ECHO'):
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Flask-Mail removed — Brevo API used instead for transactional emails

# ------------------------------------------------------------
# Authentication setup (Flask-Login)
# ------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = "login"  # endpoint name below


@login_manager.user_loader
def load_user(user_id: str):  # pragma: no cover - tiny glue helper
    """Tell Flask-Login how to load a user from a stored ID."""
    # Be defensive: ensure any prior aborted transaction is cleared
    # before attempting to load the user. Also catch DB errors and
    # rollback so a failing user load doesn't leave the request in
    # an aborted state (which causes InFailedSqlTransaction cascades).
    try:
        try:
            db.session.rollback()
        except Exception:
            app.logger.debug('load_user: pre-rollback failed', exc_info=True)

        # Use SQLAlchemy 2.0 style Session.get to avoid deprecation warnings
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    except Exception:
        app.logger.exception('load_user: DB error while loading user; rolling back')
        try:
            db.session.rollback()
        except Exception:
            app.logger.exception('load_user: rollback failed during exception handling')
        return None


# Import and register blueprints after app/db/login are ready
from copper import copper_bp  # noqa: E402
from cassiterite import cassiterite_bp  # noqa: E402
from core.routes import core_bp  # noqa: E402

app.register_blueprint(copper_bp)
app.register_blueprint(cassiterite_bp)
app.register_blueprint(core_bp)

# Template filters: translate stored review `type` and `mineral_type` into Kinyarwanda
def translate_review_type(type_value):
    if not type_value:
        return 'N/A'
    mapping = {
        'worker': 'Kwishyura Umukozi',
        'supplier': 'Kwishyura Utanga ibicuruzwa',
        'customer': 'Kwishyura Umukiriya',
        'cash_transaction': 'Cash Movement (Cashier)',
        'cash_collect_receipt': 'Collect Cash Receipt (Cashier)',
        'cash_supplier_refund': 'Supplier Refund (Cashier)',
        'other': 'Ibindi',
    }
    return mapping.get(type_value, type_value)

def translate_mineral(mineral_value):
    if not mineral_value:
        return ''
    mapping = {
        'cassiterite': 'Gasegereti',
        'coltan': 'Coltan',
        'copper': 'Coltan',
    }
    return mapping.get(mineral_value, mineral_value)


def rwanda_datetime(value, fmt='%Y-%m-%d %H:%M'):
    if not value:
        return 'N/A'
    try:
        if isinstance(value, datetime):
            return (value + timedelta(hours=2)).strftime(fmt)
        return value.strftime(fmt)
    except Exception:
        return value

app.add_template_filter(translate_review_type, name='translate_review_type')
app.add_template_filter(translate_mineral, name='translate_mineral')
app.add_template_filter(rwanda_datetime, name='rwanda_datetime')

# ============================================================
# ROUTES
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """Simple username/password login page.

    For now we authenticate by `User.username` and `User.check_password`.
    Only `is_active` users can log in.
    """


    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("This account is inactive. Please contact an administrator.", "warning")
            return render_template("auth/login.html")

        login_user(user)

        # Support ?next=/some/url redirects from login-required pages
        next_url = request.args.get("next")
        if next_url:
            return redirect(next_url)

        # Role-based default landing pages
        if user.role == "admin":
            return redirect(url_for("core.admin_users"))
        if user.role == "boss":
            return redirect(url_for("core.boss_dashboard"))
        if user.role == "store_keeper":
            return redirect(url_for("core.store_dashboard"))
        if user.role == "cashier":
            return redirect(url_for("core.cashier_dashboard"))
        if user.role == "accountant":
            # Accountants mainly work on operations; send to copper dashboard
            return redirect(url_for("copper.dashboard"))
        if user.role == "negotiator":
            return redirect(url_for("core.customer_receipts"))

        # Fallback: generic entry selector
        return redirect(url_for("entry_point"))

    # GET
    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    """Log the current user out and return to login screen."""

    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/")
def landing():
    """Public landing page.

    - Unauthenticated visitors see a friendly landing page with a small
      login form/CTA.
    - Authenticated users are redirected to their role dashboard (keeps
      existing role-redirect behaviour).
    """

    if current_user.is_authenticated:
        user = current_user
        if getattr(user, 'role', None) == "admin":
            return redirect(url_for("core.admin_users"))
        if getattr(user, 'role', None) == "boss":
            return redirect(url_for("core.boss_dashboard"))
        if getattr(user, 'role', None) == "store_keeper":
            return redirect(url_for("core.store_dashboard"))
        if getattr(user, 'role', None) == "cashier":
            return redirect(url_for("core.cashier_dashboard"))
        if getattr(user, 'role', None) == "accountant":
            return redirect(url_for("copper.dashboard"))
        if getattr(user, 'role', None) == "negotiator":
            return redirect(url_for("core.customer_receipts"))

        

        # Unauthenticated: render public landing page
    return render_template("landing.html", user_role=getattr(current_user, 'role', None))


@app.route("/entry")
@login_required
def entry_point():
    """Main application entry once logged in.

    Shows the module chooser (Copper / Cassiterite / Boss dashboard).
    """

    # Template lives directly under `templates/entry_point.html`
    return render_template("entry_point.html")







@app.route("/api/dashboard_data")
@trace_time
def api_dashboard_data():
    """API endpoint for dashboard data"""
    from copper.models import CopperStock, CopperOutput
    from core.models import BulkOutputPlan, BulkPlanStatus, CustomerReceipt
    try:
        app.logger.info("api_dashboard_data: starting")
        # Use DB-side aggregates to avoid loading full tables
        total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).scalar()
        total_output = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).scalar()

        # Single source of truth: customer outstanding = plans - receipts
        total_expected = (
            db.session.query(func.coalesce(func.sum(BulkOutputPlan.total_expected_amount), 0))
            .filter(
                BulkOutputPlan.mineral_type.in_(['copper', 'coltan']),
                BulkOutputPlan.total_expected_amount.isnot(None),
                BulkOutputPlan.total_expected_amount > 0,
                BulkOutputPlan.status.in_([BulkPlanStatus.STOCK_CONFIRMED.value, BulkPlanStatus.EXECUTED.value]),
            )
            .scalar()
            or 0.0
        )
        total_paid = (
            db.session.query(func.coalesce(func.sum(func.coalesce(CustomerReceipt.amount_rwf, CustomerReceipt.amount_input)), 0))
            .filter(CustomerReceipt.mineral_type.in_(['copper', 'coltan']))
            .scalar()
            or 0.0
        )
        total_debt = float(total_expected or 0.0) - float(total_paid or 0.0)
        stock_count = db.session.query(func.count(CopperStock.id)).scalar()
        output_count = db.session.query(func.count(CopperOutput.id)).scalar()

        app.logger.info("api_dashboard_data: completed")
        return jsonify({
            'total_input': total_input,
            'total_output': total_output,
            'total_debt': total_debt,
            'stock_count': stock_count,
            'output_count': output_count,
        })
    except Exception:
        app.logger.exception("api_dashboard_data failed")
        raise


@app.route('/supplier/<supplier>/ledger')
def supplier_ledger(supplier):
    supplier_name = (supplier or '').strip()
    if not supplier_name:
        flash('Supplier is required.', 'warning')
        return redirect(url_for('entry_point'))
    return redirect(url_for('core.consolidated_supplier_ledger_lookup', supplier=supplier_name))


@app.route('/_diag/brevo')
def diag_brevo():
    """Diagnostics for Brevo initialization.

    Returns JSON with whether the env var is present, a masked preview,
    and the error message from attempting to initialize the client.
    """
    try:
        from utils import _init_brevo_client
        import os

        api, err = _init_brevo_client()
        key = os.getenv('BREVO_API_KEY')
        preview = None
        if key:
            preview = key[:8] + '...' if len(key) > 8 else key

        return jsonify({
            'has_key': bool(key),
            'key_preview': preview,
            'init_ok': bool(api),
            'init_error': err,
        })
    except Exception as e:
        app.logger.exception('diag_brevo failed')
        return jsonify({'error': str(e)}), 500


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template('copper/404.html'), 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors"""
    return render_template('copper/500.html'), 500

@app.errorhandler(403)

def forbidden(error):
    """Handle 403 errors"""
    return render_template('403.html'), 403


# ============================================================
# CONTEXT PROCESSORS
# ============================================================


# Ensure DB session is clean at the start of each request. This prevents
# "current transaction is aborted" errors caused by leftover session state
# from previous failures.
@app.before_request
def ensure_db_session_clean():
    try:
        db.session.rollback()
    except Exception:
        # Non-fatal: log at debug level and continue
        app.logger.debug('ensure_db_session_clean: rollback failed', exc_info=True)


# Teardown handler to rollback on exceptions and remove the session.
@app.teardown_request
def shutdown_session(exception=None):
    if exception is not None:
        try:
            db.session.rollback()
        except Exception:
            app.logger.exception('shutdown_session: rollback failed')
    try:
        db.session.remove()
    except Exception:
        app.logger.exception('shutdown_session: session remove failed')


@app.context_processor
def inject_config():
    """Inject config into templates"""
    return dict(app_name="Urumuli Smart System")


@app.context_processor
def inject_nav_notifications():
    try:
        from flask_login import current_user
    except Exception:
        current_user = None

    if not current_user or not getattr(current_user, 'is_authenticated', False):
        return {
            'nav_notifications': [],
            'nav_unread_notifications_count': 0,
        }

    try:
        from core.models import fetch_user_notifications
        notifications, unread_count = fetch_user_notifications(getattr(current_user, 'id', None), unread_limit=8, read_limit=0)
        return {
            'nav_notifications': notifications or [],
            'nav_unread_notifications_count': int(unread_count or 0),
        }
    except Exception:
        return {
            'nav_notifications': [],
            'nav_unread_notifications_count': 0,
        }


# ============================================================
# CLI COMMANDS
# ============================================================

@app.cli.command()
def init_db():
    """Initialize the database"""
    db.create_all()
    print("Database initialized!")


@app.cli.command()
def seed_db():
    """Seed database with sample data (optional)"""
    print("Database seeding complete!")


@app.cli.command()
def enable_profiling():
    """Enable short profiling window: sets loggers to DEBUG and enables SQL echoing."""
    try:
        app.logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
        print("Profiling enabled: app logger DEBUG; sqlalchemy.engine INFO")
        app.logger.info("Profiling mode enabled via CLI")
    except Exception as e:
        print("Failed to enable profiling:", e)


if __name__ == "__main__":
    # Schema changes are managed by Alembic migrations.
    # Avoid db.create_all() here because the runtime DB user may not have DDL privileges.
    app.run(debug=True, host='0.0.0.0', port=5000)
