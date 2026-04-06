"""Smart Account Manager - Mining Company System

Main Flask application factory. This file wires together:
- Flask app + database
- Blueprints for minerals and core management
- Authentication (login/logout) using Flask-Login
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from config import Config, db
import logging
import os
from logging.handlers import RotatingFileHandler
from utils import trace_time
from sqlalchemy import func
from core.models import User

app = Flask(__name__)
app.config.from_object(Config)

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

app.add_template_filter(translate_review_type, name='translate_review_type')
app.add_template_filter(translate_mineral, name='translate_mineral')

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
        if user.role == "accountant":
            # Accountants mainly work on operations; send to copper dashboard
            return redirect(url_for("copper.dashboard"))

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
        if getattr(user, 'role', None) == "accountant":
            return redirect(url_for("copper.dashboard"))

        

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
    try:
        app.logger.info("api_dashboard_data: starting")
        # Use DB-side aggregates to avoid loading full tables
        total_input = db.session.query(func.coalesce(func.sum(CopperStock.input_kg), 0)).scalar()
        total_output = db.session.query(func.coalesce(func.sum(CopperOutput.output_kg), 0)).scalar()
        total_debt = db.session.query(func.coalesce(func.sum(CopperOutput.debt_remaining), 0)).scalar()
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
    """View supplier transaction ledger"""
    from copper.models import CopperStock, SupplierPayment
    try:
        app.logger.info("supplier_ledger: generating ledger for %s", supplier)
        # Fetch only required stock columns and batch-load payments to avoid N+1
        stock_rows = db.session.query(
            CopperStock.id,
            CopperStock.date,
            CopperStock.voucher_no,
            CopperStock.net_balance,
        ).filter(CopperStock.supplier == supplier).order_by(CopperStock.date).all()

        stock_ids = [r.id for r in stock_rows]
        payments = []
        if stock_ids:
            payments = db.session.query(
                SupplierPayment
            ).filter(SupplierPayment.stock_id.in_(stock_ids)).order_by(SupplierPayment.paid_at).all()

        payments_map = {}
        for p in payments:
            payments_map.setdefault(p.stock_id, []).append(p)

        # Build ledger entries in chronological order by iterating stocks and their payments
        ledger = []
        running_balance = 0
        for r in stock_rows:
            running_balance += (r.net_balance or 0)
            ledger.append({
                'date': r.date,
                'description': f"Stock {r.voucher_no}",
                'debit': r.net_balance,
                'credit': 0,
                'balance': running_balance
            })

            for payment in payments_map.get(r.id, []):
                running_balance -= payment.amount
                ledger.append({
                    'date': payment.paid_at,
                    'description': f"Payment (Ref: {payment.reference})",
                    'debit': 0,
                    'credit': payment.amount,
                    'balance': running_balance
                })

        # Totals for the ledger computed via DB aggregates to avoid re-summing
        total_owed = db.session.query(func.coalesce(func.sum(CopperStock.net_balance), 0)).filter(CopperStock.supplier == supplier).scalar()
        total_paid = db.session.query(func.coalesce(func.sum(SupplierPayment.amount), 0)).join(CopperStock, CopperStock.id == SupplierPayment.stock_id).filter(CopperStock.supplier == supplier).scalar()
        balance = (total_owed or 0) - (total_paid or 0)

        app.logger.info("supplier_ledger: completed for %s (owed=%s paid=%s)", supplier, total_owed, total_paid)

        return render_template(
            'copper/supplier_ledger.html',
            supplier=supplier,
            ledger=ledger,
            total_owed=total_owed,
            total_paid=total_paid,
            balance=balance,
            user_role=getattr(current_user, 'role', None),
        )
    except Exception:
        app.logger.exception("supplier_ledger failed for %s", supplier)
        raise


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
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
