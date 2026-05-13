#!/usr/bin/env python3
"""Debug script to verify payment history guard is working correctly."""

import sys
from config import db, app
from copper.models import CopperStock, SupplierPayment
from cassiterite.models import CassiteriteStock, CassiteriteSupplierPayment

def check_stock_payments(mineral_type, stock_id):
    """Check if a stock has any payment records."""
    print(f"\n{'='*60}")
    print(f"Checking {mineral_type.upper()} stock {stock_id}")
    print(f"{'='*60}")
    
    with app.app_context():
        if mineral_type == 'copper':
            stock = CopperStock.query.get(stock_id)
            if not stock:
                print(f"❌ Stock {stock_id} not found!")
                return
            
            print(f"✓ Stock found: {stock.voucher_no}")
            print(f"  Date: {stock.date}")
            print(f"  Supplier: {stock.supplier}")
            print(f"  Input KG: {stock.input_kg}")
            
            # Check for payments
            payments = db.session.query(SupplierPayment).filter(
                SupplierPayment.stock_id == stock_id
            ).all()
            
        elif mineral_type == 'cassiterite':
            stock = CassiteriteStock.query.get(stock_id)
            if not stock:
                print(f"❌ Stock {stock_id} not found!")
                return
            
            print(f"✓ Stock found: {stock.voucher_no}")
            print(f"  Date: {stock.date}")
            print(f"  Supplier: {stock.supplier}")
            print(f"  Input KG: {stock.input_kg}")
            
            # Check for payments
            payments = db.session.query(CassiteriteSupplierPayment).filter(
                CassiteriteSupplierPayment.stock_id == stock_id
            ).all()
        else:
            print(f"❌ Unknown mineral type: {mineral_type}")
            return
        
        print(f"\n📊 Payments for this stock:")
        print(f"   Total found: {len(payments)}")
        
        if payments:
            print(f"\n   ✓ GUARD SHOULD BLOCK EDITS (has_payments=True)")
            for i, p in enumerate(payments, 1):
                print(f"\n   Payment #{i}:")
                print(f"     - ID: {p.id}")
                print(f"     - Amount: {p.amount_rwf} RWF")
                print(f"     - Type: {p.payment_type}")
                print(f"     - Status: {p.approval_status}")
                print(f"     - Disbursed: {p.disbursement_status}")
                print(f"     - Is Deleted: {p.is_deleted}")
                print(f"     - Created By: {p.created_by_id}")
                print(f"     - Paid At: {p.paid_at}")
        else:
            print(f"\n   ❌ NO PAYMENTS FOUND - GUARD WILL ALLOW FREE EDITS")
            print(f"\n   If this stock SHOULD have payments, check:")
            print(f"   - Are payments stored in a different table?")
            print(f"   - Were payments soft-deleted?")
            print(f"   - Is the foreign key set correctly?")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python debug_payment_guard.py <mineral_type> <stock_id>")
        print("Example: python debug_payment_guard.py copper 123")
        print("Example: python debug_payment_guard.py cassiterite 45")
        sys.exit(1)
    
    mineral_type = sys.argv[1].lower()
    try:
        stock_id = int(sys.argv[2])
    except ValueError:
        print(f"❌ Stock ID must be a number, got: {sys.argv[2]}")
        sys.exit(1)
    
    check_stock_payments(mineral_type, stock_id)
