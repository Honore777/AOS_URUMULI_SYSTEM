#!/usr/bin/env python
"""Quick test setup script"""
from app import db, app
from core.models import User, CashAccount

ctx = app.app_context()
ctx.push()

# Create test users
test_users = [
    ('accountant_test', 'password123', 'accountant'),
    ('boss_test', 'password123', 'boss'),
    ('cashier_test', 'password123', 'cashier'),
]

for username, password, role in test_users:
    existing = db.session.query(User).filter_by(username=username).first()
    if not existing:
        user = User(
            username=username,
            email=f'{username}@test.local',
            role=role,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        print(f"✓ Created {username} ({role})")
    else:
        print(f"✗ {username} already exists")

# Create a test cash account
existing_account = db.session.query(CashAccount).filter_by(name='Test Cash Desk').first()
if not existing_account:
    account = CashAccount(
        name='Test Cash Desk',
        currency='RWF',
        opening_balance=10000000.0,  # 10 million RWF
        current_balance=10000000.0,  # 10 million RWF
    )
    db.session.add(account)
    print(f"✓ Created Test Cash Desk account")
else:
    print(f"✗ Test Cash Desk already exists")

db.session.commit()
print("\n✓ Setup complete!")
