"""
Rebuild Cassiterite StockAggregate to sync with current stock data
This fixes the stale aggregate issue causing dashboard to show incorrect moyenne
"""
from app import app
from config import db
from cassiterite.models import CassiteriteStock
from core.models import StockAggregate
from sqlalchemy import func

def rebuild_cassiterite_aggregate():
    """Rebuild StockAggregate for cassiterite from current stock data"""
    print("Rebuilding Cassiterite StockAggregate...")
    
    # Calculate current totals from actual stock data (filtering is_deleted=False)
    total_unit_percent = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False),
    ).scalar() or 0
    
    total_remaining_balance = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False),
    ).scalar() or 0
    
    total_t_unity = db.session.query(func.coalesce(func.sum(CassiteriteStock.t_unity), 0)).filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False),
    ).scalar() or 0
    
    # Convert to float
    total_unit_percent = float(total_unit_percent or 0.0)
    total_remaining_balance = float(total_remaining_balance or 0.0)
    total_t_unity = float(total_t_unity or 0.0)
    
    # Calculate moyenne
    moyenne = (total_unit_percent / total_remaining_balance) if total_remaining_balance else 0
    
    print(f"Current totals from DB:")
    print(f"  Total quantity: {total_remaining_balance:.2f} kg")
    print(f"  Total weighted percent: {total_unit_percent:.2f}")
    print(f"  Total t_unity: {total_t_unity:.2f}")
    print(f"  Calculated moyenne: {moyenne:.4f} ({moyenne * 100:.2f}%)")
    
    # Update or create StockAggregate
    try:
        agg = db.session.query(StockAggregate).filter_by(mineral_type='cassiterite').with_for_update().first()
        if not agg:
            agg = StockAggregate(mineral_type='cassiterite')
            db.session.add(agg)
        
        old_qty = agg.total_quantity or 0
        old_wp = agg.total_weighted_percent or 0
        
        agg.total_quantity = total_remaining_balance
        agg.total_weighted_percent = total_unit_percent
        agg.total_t_unity = total_t_unity
        
        db.session.commit()
        
        print(f"\nStockAggregate updated:")
        print(f"  Old quantity: {old_qty:.2f} kg")
        print(f"  New quantity: {total_remaining_balance:.2f} kg")
        print(f"  Old weighted percent: {old_wp:.2f}")
        print(f"  New weighted percent: {total_unit_percent:.2f}")
        print(f"\n✓ StockAggregate rebuilt successfully!")
        
    except Exception as e:
        db.session.rollback()
        print(f"\n✗ Error updating StockAggregate: {e}")
        raise

if __name__ == '__main__':
    with app.app_context():
        rebuild_cassiterite_aggregate()
