"""
Verify copper aggregate rebuild results
"""
from app import app
from config import db
from copper.models import CopperStock
from core.models import StockAggregate
from sqlalchemy import func

with app.app_context():
    print("=" * 60)
    print("COPPER AGGREGATE VERIFICATION")
    print("=" * 60)
    
    # Get aggregate values
    agg = StockAggregate.get('copper')
    if agg:
        print(f"\nStockAggregate (copper):")
        print(f"  Total quantity: {agg.total_quantity:.2f} kg")
        print(f"  Total weighted percent: {agg.total_weighted_percent:.2f}")
        print(f"  Total t_unity: {agg.total_t_unity:.2f}")
        if agg.total_quantity:
            agg_moyenne = (agg.total_weighted_percent / agg.total_quantity) * 100
            print(f"  Calculated moyenne: {agg_moyenne:.2f}%")
    
    # Query actual stock data with is_deleted=False filter
    print(f"\nActual stock data (is_deleted=False, local_balance>0):")
    total_unit_percent = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(
        CopperStock.local_balance > 0,
        CopperStock.is_deleted.is_(False),
    ).scalar() or 0
    
    total_remaining_balance = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(
        CopperStock.local_balance > 0,
        CopperStock.is_deleted.is_(False),
    ).scalar() or 0
    
    total_t_unity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(
        CopperStock.local_balance > 0,
        CopperStock.is_deleted.is_(False),
    ).scalar() or 0
    
    print(f"  Total quantity: {total_remaining_balance:.2f} kg")
    print(f"  Total weighted percent: {total_unit_percent:.2f}")
    print(f"  Total t_unity: {total_t_unity:.2f}")
    if total_remaining_balance:
        actual_moyenne = (total_unit_percent / total_remaining_balance) * 100
        print(f"  Calculated moyenne: {actual_moyenne:.2f}%")
    
    # Check for mismatch
    print(f"\n" + "=" * 60)
    if agg and abs(agg.total_quantity - total_remaining_balance) < 0.01:
        print("✓ Aggregate is in sync with actual stock data")
    else:
        print(f"✗ MISMATCH detected!")
        print(f"  Aggregate quantity: {agg.total_quantity if agg else 0:.2f} kg")
        print(f"  Actual quantity: {total_remaining_balance:.2f} kg")
        print(f"  Difference: {abs((agg.total_quantity if agg else 0) - total_remaining_balance):.2f} kg")
    
    # Count stocks
    total_stocks = CopperStock.query.filter(CopperStock.is_deleted.is_(False)).count()
    active_stocks = CopperStock.query.filter(CopperStock.local_balance > 0, CopperStock.is_deleted.is_(False)).count()
    print(f"\nStock counts:")
    print(f"  Total stocks (is_deleted=False): {total_stocks}")
    print(f"  Active stocks (local_balance>0, is_deleted=False): {active_stocks}")
