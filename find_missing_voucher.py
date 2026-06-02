#!/usr/bin/env python3
import os
from config import db
from copper.models.output import CopperOutput

# Query for SSM/TA/2826 exactly
result = CopperOutput.query.filter(
    db.func.lower(CopperOutput.voucher_no) == 'ssm/ta/2826'
).first()

print("=== Exact search: SSM/TA/2826 ===")
if result:
    print(f"Found: {result.voucher_no} | {result.output_kg}kg | Deleted: {result.is_deleted}")
else:
    print("NOT FOUND with exact match")

# Query for all 5.9kg vouchers to find typos
print("\n=== All 5.9kg vouchers (case-insensitive search) ===")
results = CopperOutput.query.filter(
    CopperOutput.output_kg == 5.9
).all()

if results:
    for r in results:
        print(f"{r.voucher_no} | {r.output_kg}kg | Deleted: {r.is_deleted} | Note: {r.note}")
else:
    print("No 5.9kg vouchers found")

# Query for similar vouchers (SSM/TA/28xx pattern)
print("\n=== Similar pattern (SSM/TA/28xx) ===")
results = CopperOutput.query.filter(
    db.func.lower(CopperOutput.voucher_no).like('ssm/ta/28%')
).all()

if results:
    for r in results:
        print(f"{r.voucher_no} | {r.output_kg}kg | Deleted: {r.is_deleted}")
else:
    print("No similar pattern found")

db.session.close()
