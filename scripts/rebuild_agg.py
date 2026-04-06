import os
import sys

# Ensure project root is on sys.path when running this script directly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app
from copper.models.stock import CopperStock
from config import db


if __name__ == '__main__':
    with app.app_context():
        agg = CopperStock.rebuild_stock_aggregate()
        db.session.commit()
        print('REBUILD RESULT:', agg)
