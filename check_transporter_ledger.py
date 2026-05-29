from app import app
from config import db
from core.models import TransporterLedger

with app.app_context():
    count = db.session.query(TransporterLedger).count()
    print(f'TransporterLedger entries: {count}')
    
    # Get the first few if they exist
    entries = db.session.query(TransporterLedger).limit(5).all()
    for e in entries:
        print(f"  - {e.transporter_name}: {e.amount_rwf} RWF ({e.entry_type})")
