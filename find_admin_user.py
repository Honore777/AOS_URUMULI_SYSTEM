"""
Find admin user in database
"""
from app import app
from config import db
from core.models import User

with app.app_context():
    admin_users = User.query.filter_by(role='admin').all()
    print("Admin users:")
    for user in admin_users:
        print(f"  ID: {user.id}, Username: {user.username}, Email: {user.email}, Active: {user.is_active}")
