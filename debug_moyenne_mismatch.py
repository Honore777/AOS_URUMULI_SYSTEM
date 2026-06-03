#!/usr/bin/env python3
"""
Debug: Investigate Moyenne Mismatch

User says they have 55.60 kg total cassiterite with 69.7% moyenne.
But optimization shows 45.89% for the same 55.60 kg.

This script will investigate:
1. How many total cassiterite stocks exist in DB
2. What the dashboard calculates for moyenne
3. What percentages are stored for each stock
4. Why there's a mismatch
"""

from app import app, db
from cassiterite.models.stock import CassiteriteStock, StockAggregate
from sqlalchemy import func

def investigate_moyenne_mismatch():
    with app.app_context():
        print("\n" + "="*80)
        print("INVESTIGATE: Moyenne Mismatch (69.7% vs 45.89%)")
        print("="*80 + "\n")
        
        # Get ALL cassiterite stocks (not deleted)
        all_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.is_deleted.is_(False)
        ).all()
        
        print(f"TOTAL CASSITERITE STOCKS IN DB: {len(all_stocks)}\n")
        
        # Get only stocks with balance > 0
        active_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.local_balance > 0,
            CassiteriteStock.is_deleted.is_(False)
        ).all()
        
        print(f"STOCKS WITH BALANCE > 0: {len(active_stocks)}\n")
        
        # Calculate dashboard moyenne manually
        sum_unit_percent = sum(s.unit_percent or 0 for s in active_stocks)
        sum_local_balance = sum(s.local_balance or 0 for s in active_stocks)
        dashboard_moyenne_manual = (sum_unit_percent / sum_local_balance) if sum_local_balance > 0 else 0
        
        print("DASHBOARD CALCULATION (from active stocks):")
        print(f"  Sum of unit_percent:     {sum_unit_percent:.10f}")
        print(f"  Sum of local_balance:    {sum_local_balance:.2f} kg")
        print(f"  Calculated Moyenne:      {dashboard_moyenne_manual * 100:.4f}%\n")
        
        # Check StockAggregate
        agg = StockAggregate.get('cassiterite')
        if agg:
            dashboard_moyenne_agg = (agg.total_weighted_percent / agg.total_quantity) if agg.total_quantity else 0
            print("DASHBOARD CALCULATION (from StockAggregate cache):")
            print(f"  StockAggregate.total_weighted_percent: {agg.total_weighted_percent:.10f}")
            print(f"  StockAggregate.total_quantity:         {agg.total_quantity:.2f} kg")
            print(f"  Cached Moyenne:                        {dashboard_moyenne_agg * 100:.4f}%\n")
        
        # Show all active stocks details
        print("ALL ACTIVE STOCKS:")
        print(f"{'ID':<4} {'Voucher':<15} {'Supplier':<25} {'%':<8} {'Balance':<10} {'Unit%':<15}")
        print("-" * 80)
        total_balance_check = 0
        total_unit_check = 0
        for s in active_stocks:
            pct = (s.percentage or 0) * 100
            unit_pct = s.unit_percent or 0
            total_balance_check += s.local_balance or 0
            total_unit_check += unit_pct
            supplier = (s.supplier or "")[:23]
            voucher = (s.voucher_no or "")[:13]
            print(f"{s.id:<4} {voucher:<15} {supplier:<25} {pct:<8.2f} {s.local_balance:<10.2f} {unit_pct:<15.6f}")
        
        print("-" * 80)
        print(f"{'TOTAL':<54} {total_balance_check:<10.2f} {total_unit_check:<15.6f}\n")
        
        # Check if there are OTHER stocks not shown in optimization
        print("CHECK: Optimization Selection")
        opt_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.id.in_([11, 10, 14, 17]),  # The stocks from optimization
            CassiteriteStock.is_deleted.is_(False)
        ).all()
        
        print(f"Stocks selected in optimization: {len(opt_stocks)}")
        opt_sum_unit = sum(s.unit_percent or 0 for s in opt_stocks)
        opt_sum_balance = sum(s.local_balance or 0 for s in opt_stocks)
        opt_moyenne = (opt_sum_unit / opt_sum_balance) if opt_sum_balance > 0 else 0
        
        print(f"  Sum unit_percent: {opt_sum_unit:.6f}")
        print(f"  Sum balance: {opt_sum_balance:.2f} kg")
        print(f"  Achieved moyenne: {opt_moyenne * 100:.4f}%")
        print(f"  Expected (from table): 45.89%\n")
        
        # Check percentage values stored
        print("VERIFICATION: Are percentage values correct?")
        for stock_id in [11, 10, 14, 17]:
            s = CassiteriteStock.query.filter(CassiteriteStock.id == stock_id).first()
            if s:
                calc_unit_pct = (s.local_balance * s.percentage / 100) if s.percentage else 0
                stored_unit_pct = s.unit_percent or 0
                match = "✓" if abs(calc_unit_pct - stored_unit_pct) < 0.001 else "✗ MISMATCH"
                print(f"  Stock {s.id}:")
                print(f"    Stored percentage: {(s.percentage or 0)*100:.2f}%")
                print(f"    Stored unit_percent: {stored_unit_pct:.6f}")
                print(f"    Calculated unit_percent: {calc_unit_pct:.6f}")
                print(f"    {match}\n")
        
        # HYPOTHESIS
        print("HYPOTHESIS:")
        if abs(sum_local_balance - 55.60) > 0.01:
            print(f"  ✗ Total balance in DB ({sum_local_balance:.2f}) != user's total (55.60)")
            print(f"    Difference: {sum_local_balance - 55.60:.2f} kg")
            print(f"    → Dashboard might include hidden stocks!\n")
        else:
            print(f"  ✓ Total balance matches (55.60 kg)")
            print(f"    → All stocks are visible\n")
        
        if abs(dashboard_moyenne_manual * 100 - 69.7) > 0.1:
            print(f"  ✗ Calculated moyenne ({dashboard_moyenne_manual*100:.4f}%) != shown (69.7%)")
            print(f"    → Either:")
            print(f"      1. Dashboard is cached from different date")
            print(f"      2. Some stocks are soft-deleted but cache not updated")
            print(f"      3. Unit_percent not calculated correctly on stock creation\n")
        else:
            print(f"  ✓ Calculated moyenne matches dashboard\n")
        
        # Check for soft-deleted stocks
        deleted_stocks = CassiteriteStock.query.filter(
            CassiteriteStock.is_deleted.is_(True)
        ).all()
        
        if deleted_stocks:
            print(f"DELETED STOCKS: {len(deleted_stocks)} stocks marked as deleted")
            print("  These are EXCLUDED from calculations\n")
        
        print("="*80 + "\n")

if __name__ == '__main__':
    investigate_moyenne_mismatch()
