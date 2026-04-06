import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

print('CHECK_AGG START')
import time
from app import app
from core.models import StockAggregate

if __name__ == '__main__':
    with app.app_context():
        agg = StockAggregate.get('copper')
        print('AGG RAW:', repr(agg))
        if agg:
            print('AGG FIELDS -> total_quantity:', agg.total_quantity, 'total_weighted_percent:', agg.total_weighted_percent, 'total_t_unity:', agg.total_t_unity)
        else:
            print('agg is None')
        time.sleep(0.01)
