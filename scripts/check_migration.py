from config import db
from sqlalchemy import inspect
ins = inspect(db.engine)
print('has_batch_deduction:', 'batch_deduction' in ins.get_table_names())
if 'bulk_output_plan' in ins.get_table_names():
    cols = [c['name'] for c in ins.get_columns('bulk_output_plan')]
    print('bulk_output_plan columns:', cols)
else:
    print('bulk_output_plan missing')
