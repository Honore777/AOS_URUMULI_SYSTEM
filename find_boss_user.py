"""
Find boss user in database
"""
from app import app
from config import db
from core.models import User

with app.app_context():
    boss_users = User.query.filter_by(role='boss').all()
    print("Boss users:")
    for user in boss_users:
        print(f"  ID: {user.id}, Username: {user.username}, Email: {user.email}, Active: {user.is_active}")
