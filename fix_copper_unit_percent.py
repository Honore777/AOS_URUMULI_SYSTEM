#!/usr/bin/env python3
"""
Fix Copper/Coltan Unit Percent Calculation

This script repairs all copper/coltan stocks that have incorrect unit_percent values.
The bug was in calculate_unit_percentage which did:
    unit_percent = local_balance * percentage
    
Instead of:
    unit_percent = local_balance * (percentage / 100)

This caused all unit_percent values to be multiplied by 100.

This script:
1. Calculates the correct unit_percent for each stock
2. Updates the stock record
3. Rebuilds the StockAggregate cache
4. Verifies the fix worked
"""

from app import app, db
from copper.models.stock import CopperStock
from core.models import StockAggregate

def fix_copper_unit_percent():
    """Fix all copper/coltan stocks with incorrect unit_percent values."""
    
    with app.app_context():
        print("\n" + "="*80)
        print("COPPER/COLTAN UNIT PERCENT FIX")
        print("="*80 + "\n")
        
        # Get all copper/coltan stocks (including deleted)
        all_stocks = CopperStock.query.all()
        
        if not all_stocks:
            print("No copper/coltan stocks found!")
            return
        
        print(f"Found {len(all_stocks)} total stocks\n")
        
        # Calculate and update each stock
        fixed_count = 0
        total_old_unit_percent = 0.0
        total_new_unit_percent = 0.0
        
        for stock in all_stocks:
            old_unit_percent = stock.unit_percent or 0.0
            
            # Calculate correct unit_percent
            if stock.local_balance and stock.percentage:
                new_unit_percent = stock.local_balance * (stock.percentage / 100)
            else:
                new_unit_percent = 0.0
            
            # Update if different
            if abs(new_unit_percent - old_unit_percent) > 0.001:
                stock.unit_percent = new_unit_percent
                fixed_count += 1
                
                if fixed_count <= 10:  # Print first 10
                    print(f"Stock {stock.id} ({stock.supplier}):")
                    print(f"  Old unit_percent: {old_unit_percent:.6f}")
                    print(f"  New unit_percent: {new_unit_percent:.6f}")
                    print(f"  Correction: ÷ 100")
                    print()
            
            total_old_unit_percent += old_unit_percent
            total_new_unit_percent += new_unit_percent
        
        if fixed_count > 10:
            print(f"... and {fixed_count - 10} more stocks fixed\n")
        
        print(f"SUMMARY:")
        print(f"  Stocks fixed: {fixed_count}/{len(all_stocks)}")
        print(f"  Total old sum(unit_percent): {total_old_unit_percent:.6f}")
        print(f"  Total new sum(unit_percent): {total_new_unit_percent:.6f}")
        print(f"  Correction factor: {total_old_unit_percent / total_new_unit_percent:.2f}x (should be ~100)\n")
        
        # Commit the updates
        if fixed_count > 0:
            print(f"Saving {fixed_count} corrected stocks...")
            db.session.commit()
            print("✓ Stocks saved!\n")
        else:
            print("No stocks needed fixing.\n")
        
        # Rebuild the aggregate
        print("Rebuilding StockAggregate...")
        agg = CopperStock.rebuild_stock_aggregate()
        if agg:
            print(f"✓ Aggregate rebuilt!")
            print(f"  New total_quantity: {agg.total_quantity:.2f} kg")
            print(f"  New total_weighted_percent: {agg.total_weighted_percent:.6f}")
            
            if agg.total_quantity and agg.total_quantity > 0:
                new_moyenne = agg.total_weighted_percent / agg.total_quantity
                print(f"  Calculated Moyenne: {new_moyenne * 100:.4f}%\n")
            else:
                print()
        else:
            print("✗ Failed to rebuild aggregate\n")
        
        # Verify with manual calculation
        print("VERIFICATION:")
        active_stocks = CopperStock.query.filter(
            CopperStock.local_balance > 0,
            CopperStock.is_deleted.is_(False)
        ).all()
        
        if active_stocks:
            sum_unit = sum(s.unit_percent or 0 for s in active_stocks)
            sum_balance = sum(s.local_balance or 0 for s in active_stocks)
            calculated_moyenne = (sum_unit / sum_balance * 100) if sum_balance > 0 else 0
            
            print(f"  Active stocks with balance > 0: {len(active_stocks)}")
            print(f"  Sum of unit_percent: {sum_unit:.6f}")
            print(f"  Sum of local_balance: {sum_balance:.2f} kg")
            print(f"  Calculated Moyenne: {calculated_moyenne:.4f}%")
        
        print("\n" + "="*80 + "\n")

if __name__ == '__main__':
    fix_copper_unit_percent()
