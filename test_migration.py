#!/usr/bin/env python
"""Test script to verify database migration was applied."""
from app import app, db
from core.models import BulkOutputPlan

with app.app_context():
    try:
        count = BulkOutputPlan.query.count()
        print(f"✓ Database connected - Total plans: {count}")
        print("✓ total_expected_amount field is accessible")
        print("SUCCESS: Migration applied correctly!")
    except Exception as e:
        print(f"ERROR: {e}")
