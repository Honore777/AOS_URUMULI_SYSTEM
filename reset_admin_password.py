"""
Reset admin password
"""
from app import app
from config import db
from core.models import User

with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    if admin:
        new_password = "admin123"  # Change this to your desired password
        admin.set_password(new_password)
        db.session.commit()
        print(f"Password reset for admin user (ID: {admin.id})")
        print(f"New password: {new_password}")
    else:
        print("Admin user not found")
