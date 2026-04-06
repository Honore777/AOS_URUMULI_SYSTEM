import sys

# ensure project root is on sys.path so `import app` works when running
# this script directly from different working directories
sys.path.append(r'C:\Users\USER\final_smart_account_manager')

from app import app, db

if __name__ == '__main__':
    with app.app_context():
        from copper.models import CopperStock
        from cassiterite.models import CassiteriteStock

        print('Backfilling stock_aggregate for copper...')
        try:
            agg_c = CopperStock.rebuild_stock_aggregate()
            print('Copper agg:', (agg_c.total_quantity, agg_c.total_weighted_percent, agg_c.total_t_unity) if agg_c else None)
        except Exception as e:
            print('Copper backfill failed:', e)

        print('Backfilling stock_aggregate for cassiterite...')
        try:
            agg_ca = CassiteriteStock.rebuild_stock_aggregate()
            print('Cassiterite agg:', (agg_ca.total_quantity, agg_ca.total_weighted_percent, agg_ca.total_t_unity) if agg_ca else None)
        except Exception as e:
            print('Cassiterite backfill failed:', e)

        print('Backfill complete.')
