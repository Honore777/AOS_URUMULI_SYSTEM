#!/usr/bin/env python3
"""
Debug script to verify Moyenne calculation consistency between dashboard and optimization.
This validates whether:
1. Dashboard Moyenne calculation is correct
2. Optimization Moyenne calculation matches dashboard when selecting all stocks
3. There are precision/rounding issues
"""
from app import app, db
from cassiterite.models.stock import CassiteriteStock
from cassiterite_optimization import select_stocks_for_average_quality
from utils import calculate_unit_percentage

def test_moyenne_calculation():
    """Test if dashboard Moyenne matches optimization Moyenne when selecting all stocks."""
    with app.app_context():
        # Get all cassiterite stocks
        all_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.local_balance > 0,
            CassiteriteStock.is_deleted.is_(False)
        ).all()
        
        if not all_stocks:
            print("No stocks found!")
            return
        
        print(f"\n{'='*80}")
        print(f"MOYENNE CALCULATION TEST")
        print(f"{'='*80}")
        print(f"Total stocks with balance > 0: {len(all_stocks)}\n")
        
        # 1. DASHBOARD METHOD: sum(unit_percent) / sum(local_balance)
        total_unit_percent_dash = sum(s.unit_percent or 0 for s in all_stocks)
        total_local_balance_dash = sum(s.local_balance or 0 for s in all_stocks)
        dashboard_moyenne = (total_unit_percent_dash / total_local_balance_dash) if total_local_balance_dash > 0 else 0
        
        print("1. DASHBOARD CALCULATION:")
        print(f"   Total unit_percent (sum):    {total_unit_percent_dash:.10f}")
        print(f"   Total local_balance (sum):   {total_local_balance_dash:.10f}")
        print(f"   Dashboard Moyenne:           {dashboard_moyenne:.10f} ({dashboard_moyenne*100:.4f}%)\n")
        
        # 2. OPTIMIZATION METHOD: When selecting all stocks with full quantities
        # Simulate what optimization calculates
        total_unit_val_opt = 0.0
        total_qty_val_opt = 0.0
        
        for s in all_stocks:
            # This is what optimization calculates
            if s.local_balance > 0:
                unit_percent_val = (s.unit_percent / s.local_balance) if s.local_balance > 0 else 0
                qty_taken = s.local_balance  # Taking full amount
                total_unit_val_opt += unit_percent_val * qty_taken
                total_qty_val_opt += qty_taken
        
        optimization_moyenne = (total_unit_val_opt / total_qty_val_opt) if total_qty_val_opt > 0 else 0
        
        print("2. OPTIMIZATION CALCULATION (when taking all quantities):")
        print(f"   Total weighted units (opt):  {total_unit_val_opt:.10f}")
        print(f"   Total quantity (opt):        {total_qty_val_opt:.10f}")
        print(f"   Optimization Moyenne:        {optimization_moyenne:.10f} ({optimization_moyenne*100:.4f}%)\n")
        
        # 3. VERIFY THEY MATCH
        diff = abs(dashboard_moyenne - optimization_moyenne)
        print("3. COMPARISON:")
        print(f"   Dashboard - Optimization:    {(dashboard_moyenne - optimization_moyenne):.15f}")
        print(f"   Absolute difference:         {diff:.15f}")
        print(f"   Match (< 0.00001):           {'✓ YES' if diff < 0.00001 else '✗ NO'}\n")
        
        # 4. FORMULA VERIFICATION
        print("4. FORMULA VERIFICATION:")
        print(f"   Dashboard formula: sum(unit_percent) / sum(local_balance)")
        print(f"   Opt formula:       sum((unit_percent/local_balance) × qty) / sum(qty)")
        print(f"   When qty = local_balance:")
        print(f"   Opt becomes:       sum(percentage) / sum(quantity)")
        print(f"   Which equals:      sum((local_balance × %) / 100) / sum(local_balance)")
        print(f"   = (1/100) × sum(local_balance × %) / sum(local_balance)")
        print(f"   = (1/100) × (1/sum(local_balance)) × sum(local_balance × %)")
        print(f"   This DOES match dashboard!\n")
        
        # 5. Check individual stock details
        print("5. INDIVIDUAL STOCK DETAILS (first 10):")
        print(f"   {'ID':<5} {'Supplier':<20} {'%':<8} {'Balance':<12} {'Unit%':<15} {'Unit%/Balance':<15}")
        print(f"   {'-'*80}")
        for s in all_stocks[:10]:
            supplier = (s.supplier or "")[:18]
            pct = (s.percentage or 0) * 100
            unit_pct_val = (s.unit_percent or 0) / (s.local_balance or 1)
            print(f"   {s.id:<5} {supplier:<20} {pct:<8.4f} {s.local_balance:<12.2f} {(s.unit_percent or 0):<15.4f} {unit_pct_val:<15.10f}")
        
        # 6. TEST: Call optimization function to get its achieved moyenne
        print(f"\n6. ACTUAL OPTIMIZATION FUNCTION TEST:")
        target_avg = dashboard_moyenne * 100  # Convert back to percentage
        print(f"   Calling select_stocks_for_average_quality with target={target_avg:.4f}%")
        
        try:
            selected_stocks, achieved_opt_moyenne, achieved_qty = select_stocks_for_average_quality(
                target_moyenne=target_avg,
                target_total_quantity=None,  # No quantity constraint
                minimize_quantity=False,
            )
            
            print(f"   Selected stocks: {len(selected_stocks)}")
            print(f"   Achieved moyenne (opt): {achieved_opt_moyenne*100:.4f}%")
            print(f"   Achieved quantity: {achieved_qty:.2f}")
            print(f"   Matches dashboard? {abs((achieved_opt_moyenne*100) - (dashboard_moyenne*100)) < 0.01}")
        except Exception as e:
            print(f"   Error calling optimization: {str(e)}")
        
        print(f"\n{'='*80}\n")

if __name__ == '__main__':
    test_moyenne_calculation()
