# Alembic env.py before/after
# --- ORIGINAL (problematic) ---
# This pattern accesses `current_app` at import-time. Running `alembic` from
# the CLI (outside a Flask app context) raises "Working outside of application context".

# (excerpt from migrations/env.py)
from flask import current_app

config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# ... later
conf_args = current_app.extensions['migrate'].configure_args
connectable = get_engine()

# Running alembic CLI fails because current_app is not bound.


# --- PATCHED (fix) ---
# Two safe approaches are shown. Pick one and place it into migrations/env.py

# Approach A: create the app via the factory and push an app context when needed.
# This requires your project to expose a `create_app()` function in app.py (or similar).

# from app import create_app
# from config import db as app_db
#
# app = create_app()
#
# with app.app_context():
#     config.set_main_option('sqlalchemy.url', str(app_db.engine.url).replace('%', '%%'))
#     target_db = app_db
#
#     # Then the rest of env.py can call get_metadata() and run migrations using target_db

# Approach B: lazily create an app context only when current_app is unavailable.
# (This is resilient and does not require changing how you normally run Flask.)

# import logging
# from logging.config import fileConfig
#
# try:
#     from flask import current_app
# except Exception:
#     current_app = None
#
# # If Alembic is invoked outside Flask, create app and push context
# def ensure_app_context():
#     global current_app
#     try:
#         # if current_app is available and bound, this will succeed
#         _ = current_app.name
#     except Exception:
#         # create app lazily (adjust import as needed for your project)
#         from app import create_app
#         from config import db as app_db
#         app = create_app()
#         app.app_context().push()
#         current_app = app
#         return app_db
#     else:
#         return current_app.extensions['migrate'].db
#
# # usage in env.py
# app_db = ensure_app_context()
# config.set_main_option('sqlalchemy.url', str(app_db.get_engine().url).replace('%', '%%'))
# target_db = app_db

# Notes: adjust `from app import create_app` and `from config import db` to match your project layout.
# Both approaches avoid accessing `current_app` at module-import time, and instead ensure a Flask
# application context exists when Alembic needs the SQLAlchemy engine.
