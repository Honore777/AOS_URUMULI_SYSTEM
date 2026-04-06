import sys
sys.path.append(r'C:\Users\USER\final_smart_account_manager')

try:
    from app import app, db
    with app.app_context():
        from cassiterite.models import CassiteriteStock, CassiteriteOutput
        print('IMPORT_OK', CassiteriteStock.__name__, CassiteriteOutput.__name__)
except Exception as e:
    import traceback
    traceback.print_exc()
    print('IMPORT_FAILED', e)
