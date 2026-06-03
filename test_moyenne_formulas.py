#!/usr/bin/env python3
"""
Simplified test to verify Moyenne calculation formulas are mathematically equivalent.
"""
from app import app, db
from cassiterite.models.stock import CassiteriteStock

def test_formulas():
    """Verify dashboard and optimization formulas are equivalent."""
    with app.app_context():
        # Get all cassiterite stocks
        all_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.local_balance > 0,
            CassiteriteStock.is_deleted.is_(False)
        ).all()
        
        if not all_stocks:
            print("No stocks found in database!")
            return
        
        print(f"\n{'='*80}")
        print(f"MOYENNE FORMULA EQUIVALENCE TEST")
        print(f"{'='*80}")
        print(f"Total stocks with balance > 0: {len(all_stocks)}\n")
        
        # Dashboard: moyenne = sum(unit_percent) / sum(local_balance)
        sum_unit_percent = sum(s.unit_percent or 0 for s in all_stocks)
        sum_local_balance = sum(s.local_balance or 0 for s in all_stocks)
        dashboard_moyenne = (sum_unit_percent / sum_local_balance) if sum_local_balance > 0 else 0
        
        # Optimization with all quantities: sum((unit_percent/local_balance) * qty) / sum(qty)
        # When qty = local_balance for all:
        sum_weighted = 0.0
        sum_qty = 0.0
        for s in all_stocks:
            if s.local_balance > 0:
                qty = s.local_balance
                unit_percent_ratio = (s.unit_percent or 0) / s.local_balance
                sum_weighted += unit_percent_ratio * qty
                sum_qty += qty
        
        optimization_moyenne = (sum_weighted / sum_qty) if sum_qty > 0 else 0
        
        print("DASHBOARD FORMULA:")
        print(f"  moyenne = sum(unit_percent) / sum(local_balance)")
        print(f"  = {sum_unit_percent:.10f} / {sum_local_balance:.10f}")
        print(f"  = {dashboard_moyenne:.10f}\n")
        
        print("OPTIMIZATION FORMULA (with qty = local_balance for all):")
        print(f"  moyenne = sum((unit_percent/local_balance) × qty) / sum(qty)")
        print(f"  When qty = local_balance:")
        print(f"  = sum((unit_percent/local_balance) × local_balance) / sum(local_balance)")
        print(f"  = sum(unit_percent) / sum(local_balance)")
        print(f"  = {sum_weighted:.10f} / {sum_qty:.10f}")
        print(f"  = {optimization_moyenne:.10f}\n")
        
        diff = abs(dashboard_moyenne - optimization_moyenne)
        print("RESULT:")
        print(f"  Dashboard:    {dashboard_moyenne * 100:.10f}%")
        print(f"  Optimization: {optimization_moyenne * 100:.10f}%")
        print(f"  Difference:   {diff * 100:.15f}%")
        print(f"  Match:        {'✓ YES (formulas are equivalent)' if diff < 0.00000001 else '✗ NO (formulas differ!)'}\n")
        
        # Now check: what if optimization is selecting a SUBSET?
        print("ISSUE HYPOTHESIS: What if optimization selects a SUBSET of stocks?")
        print(f"  If user enters target_moyenne = {dashboard_moyenne*100:.4f}%")
        print(f"  But optimization selects only some stocks with higher/lower percentages,")
        print(f"  Then achieved_moyenne might be different!\n")
        
        # Check if percentages vary significantly
        percentages = [s.percentage * 100 for s in all_stocks if s.percentage]
        if percentages:
            min_pct = min(percentages)
            max_pct = max(percentages)
            avg_pct = sum(percentages) / len(percentages)
            print(f"PERCENTAGE DISTRIBUTION:")
            print(f"  Min percentage:    {min_pct:.4f}%")
            print(f"  Max percentage:    {max_pct:.4f}%")
            print(f"  Average percentage: {avg_pct:.4f}%")
            print(f"  Dashboard moyenne:  {dashboard_moyenne*100:.4f}%")
            print(f"  Range:             {max_pct - min_pct:.4f}%\n")
            
            if abs(max_pct - min_pct) > 0.01:
                print("  → Percentages vary significantly! Optimization might select subset→ Could explain Moyenne mismatch!\n")
        
        print(f"{'='*80}\n")

if __name__ == '__main__':
    test_formulas()
