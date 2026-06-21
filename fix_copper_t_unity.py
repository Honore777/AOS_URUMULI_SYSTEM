#!/usr/bin/env python3
"""
Fix Copper/Coltan T_Unity Calculation

This script repairs all copper/coltan stocks that have incorrect t_unity values.
The formula should be: t_unity = nb * local_balance
"""

from app import app, db
from copper.models.stock import CopperStock
from core.models import StockAggregate

def fix_copper_t_unity():
    """Fix all copper/coltan stocks with incorrect t_unity values."""
    
    with app.app_context():
        print("\n" + "="*80)
        print("COPPER/COLTAN T_UNITY FIX")
        print("="*80 + "\n")
        
        # Get all copper/coltan stocks (including deleted)
        all_stocks = CopperStock.query.all()
        
        if not all_stocks:
            print("No copper/coltan stocks found!")
            return
        
        print(f"Found {len(all_stocks)} total stocks\n")
        
        # Calculate and update each stock
        fixed_count = 0
        total_old_t_unity = 0.0
        total_new_t_unity = 0.0
        
        for stock in all_stocks:
            old_t_unity = stock.t_unity or 0.0
            
            # Calculate correct t_unity
            if stock.nb and stock.local_balance:
                new_t_unity = stock.nb * stock.local_balance
            else:
                new_t_unity = 0.0
            
            # Update if different
            if abs(new_t_unity - old_t_unity) > 0.001:
                stock.t_unity = new_t_unity
                fixed_count += 1
                
                if fixed_count <= 10:  # Print first 10
                    print(f"Stock {stock.id} ({stock.supplier}):")
                    print(f"  Old t_unity: {old_t_unity:.6f}")
                    print(f"  New t_unity: {new_t_unity:.6f}")
                    print(f"  Correction: t_unity = nb * local_balance")
                    print()
            
            total_old_t_unity += old_t_unity
            total_new_t_unity += new_t_unity
        
        if fixed_count > 10:
            print(f"... and {fixed_count - 10} more stocks fixed\n")
        
        print(f"SUMMARY:")
        print(f"  Stocks fixed: {fixed_count}/{len(all_stocks)}")
        print(f"  Total old sum(t_unity): {total_old_t_unity:.6f}")
        print(f"  Total new sum(t_unity): {total_new_t_unity:.6f}")
        print(f"  Difference: {total_new_t_unity - total_old_t_unity:.6f}\n")
        
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
            print(f"  New total_t_unity: {agg.total_t_unity:.6f}")
            
            if agg.total_quantity and agg.total_quantity > 0:
                new_moyenne_nb = agg.total_t_unity / agg.total_quantity
                print(f"  Calculated Moyenne NB: {new_moyenne_nb:.4f}\n")
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
            sum_t_unity = sum(s.t_unity or 0 for s in active_stocks)
            sum_balance = sum(s.local_balance or 0 for s in active_stocks)
            calculated_moyenne_nb = (sum_t_unity / sum_balance) if sum_balance > 0 else 0
            
            print(f"  Active stocks with balance > 0: {len(active_stocks)}")
            print(f"  Sum of t_unity: {sum_t_unity:.6f}")
            print(f"  Sum of local_balance: {sum_balance:.2f} kg")
            print(f"  Calculated Moyenne NB: {calculated_moyenne_nb:.4f}")
        
        print("\n" + "="*80 + "\n")

if __name__ == '__main__':
    fix_copper_t_unity()
