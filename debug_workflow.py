#!/usr/bin/env python
"""
Debug script to show workflow and current database state
"""
from app import app, db
from sqlalchemy import text

with app.app_context():
    print("\n" + "=" * 110)
    print("WORKFLOW EXPLANATION: Complete Flow with Database State")
    print("=" * 110)
    
    print("\n\n" + "█" * 110)
    print("█ STEP 1: STORE KEEPER OUTPUTS STOCK → Creates BulkOutputPlan record")
    print("█" * 110)
    print("""
    TABLE: bulk_output_plan
    Fields needed:
      - id (primary key)
      - batch_id: String identifier (e.g., 'batch_20260518_37e140')
      - mineral_type: 'copper' or 'cassiterite'
      - customer: Customer name (NEGOTIATOR FILLS THIS IN LATER, initially NULL)
      - total_expected_amount: Final agreed amount (FILLED LATER BY BOSS, initially 0/NULL)
      - status: STOCK_CONFIRMED or EXECUTED (must be this to appear in negotiator views)
      - currency: RWF or USD
      - created_at: When the plan was created
    
    Current database state:
    """)
    
    result = db.session.execute(text("""
        SELECT id, batch_id, mineral_type, customer, total_expected_amount, status, created_at
        FROM bulk_output_plan
        WHERE status IN ('STOCK_CONFIRMED', 'EXECUTED')
        ORDER BY created_at DESC LIMIT 5;
    """)).fetchall()
    
    if result:
        for i, row in enumerate(result, 1):
            print(f"\n    Plan #{row[0]:3d}: batch={row[1]}, mineral={row[2]:10s}, customer={str(row[3]):25s}, agreement={row[4]}, status={row[5]}")
    else:
        print("\n    → NO PLANS IN DATABASE YET")
    
    print("\n\n" + "█" * 110)
    print("█ STEP 2: NEGOTIATOR ENTERS BATCH + CUSTOMER (but NO agreed amount)")
    print("█" * 110)
    print("""
    WHERE IS THIS HAPPENING?
    
    Looking for the route that updates BulkOutputPlan.customer...
    This should happen when negotiator enters the customer name in customer_receipts page
    
    EXPECTED BEHAVIOR:
      1. Negotiator goes to /receipts/customer (customer_receipts route)
      2. Selects a batch with status=STOCK_CONFIRMED or EXECUTED
      3. Enters a customer name (e.g., "ABC Mining Company")
      4. Submits WITHOUT entering an agreed total (leaves it blank)
      5. This should UPDATE: BulkOutputPlan.customer = "ABC Mining Company"
      6. total_expected_amount stays 0 or NULL
      7. Creates a request for boss approval
    
    THE ERROR MESSAGE YOU SEE:
      "No agreed total entered yet. Use Advance Only now, then come back later..."
      
      This is from line 3634 in core/routes/management.py:
    """)
    
    print("""
    ┌─────────────────────────────────────────────────────────────────┐
    │ CODE: core/routes/management.py, customer_receipts route        │
    │ Line 3634-3639                                                  │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  try:                                                            │
    │      total_expected_amount = float(request.form.get("total_e   │
    │  except ValueError:                                             │
    │      total_expected_amount = 0.0                                │
    │                                                                  │
    │  if total_expected_amount <= 0:                                 │
    │      # No agreed total yet: fall back to advance-only flow     │
    │      advance_amount_present = float(request.form.get(          │
    │                           'advance_amount_input') or 0) > 0     │
    │      if advance_amount_present:                                 │
    │          action_type = 'advance_only'                           │
    │      else:                                                       │
    │          # ← THIS MESSAGE SHOWS UP                             │
    │          flash("No agreed total entered yet. Use Advance Only..│
    │          return redirect(url_for("core.customer_receipts"))    │
    │                                                                  │
    │ TRANSLATION:                                                    │
    │   If total_expected_amount <= 0 AND no advance amount entered  │
    │   → Show error and redirect back                                │
    │                                                                  │
    │ SOLUTION:                                                        │
    │   When entering just the customer WITHOUT amounts:             │
    │   → Check the "Advance Only" checkbox OR                        │
    │   → Enter an Advance Amount                                     │
    │                                                                  │
    └─────────────────────────────────────────────────────────────────┘
    """)
    
    print("\n\n" + "█" * 110)
    print("█ STEP 3: CUSTOMER SHOULD APPEAR IN 'UPDATE DEBTS' DROPDOWN")
    print("█" * 110)
    print("""
    THE FUNCTION: _batch_debt_options() at line 5077
    
    ┌─────────────────────────────────────────────────────────────────┐
    │ This function queries for customers that appear in dropdown     │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  Filters:                                                        │
    │    1. BulkOutputPlan.customer IS NOT NULL   ← Must have name   │
    │    2. BulkOutputPlan.status IN (STOCK_CONFIRMED, EXECUTED)     │
    │    3. BulkOutputPlan.mineral_type IN (copper, cassiterite)     │
    │                                                                  │
    │  Visibility rule (line 5146):                                   │
    │    row_visible = remaining_rwf > 0.01 OR planned_total <= 0.01 │
    │                                         ↑ THIS ALLOWS SHOWING  │
    │                                           CUSTOMERS WITH NO     │
    │                                           AGREEMENT YET!        │
    │                                                                  │
    │ CURRENT DATABASE STATE - Customers eligible for UPDATE DEBTS:   │
    """)
    
    result = db.session.execute(text("""
        SELECT id, batch_id, customer, mineral_type, total_expected_amount, status
        FROM bulk_output_plan
        WHERE customer IS NOT NULL
        AND status IN ('STOCK_CONFIRMED', 'EXECUTED')
        AND mineral_type IN ('copper', 'cassiterite')
        ORDER BY created_at DESC
        LIMIT 5;
    """)).fetchall()
    
    if result:
        for i, row in enumerate(result, 1):
            print(f"\n    {i}. Plan #{row[0]}: customer='{row[2]}', batch={row[1]}, mineral={row[3]}, agreement={row[4]}")
    else:
        print("\n    → NO CUSTOMERS FOUND YET IN UPDATE DEBTS")
        print("       WHY? Because BulkOutputPlan.customer is still NULL")
        print("       FIX: Make sure negotiator submits customer name to update plan.customer")
    
    print("""
    │                                                                  │
    └─────────────────────────────────────────────────────────────────┘
    """)
    
    print("\n\n" + "█" * 110)
    print("█ STEP 4: NEGOTIATOR RECORDS ADVANCE IN 'UPDATE DEBTS' (with receipt_type=ADVANCE)")
    print("█" * 110)
    print("""
    When negotiator selects customer from dropdown and checks "Advance Only":
    
    CODE: update_debts() route at line 5256
    
    ┌──────────────────────────────────────────────────────────────────┐
    │ if receipt_type == CustomerReceiptType.ADVANCE.value and        │
    │    outstanding_rwf <= 0:                                         │
    │                                                                   │
    │    # Create unearned customer receipt (advance before agreement) │
    │    _create_customer_unearned_receipt(...)                        │
    │    → Creates CustomerReceipt with receipt_type='ADVANCE'        │
    │    → Amount stored in RWF                                        │
    │    → Visible in customer ledger                                  │
    │                                                                   │
    │ TABLE: customer_receipt (records all advances & payments)       │
    │   - id                                                            │
    │   - customer: Customer name                                      │
    │   - batch_id: Which batch this is for                            │
    │   - amount_rwf: Amount in RWF                                    │
    │   - currency: Original currency (RWF or USD)                     │
    │   - exchange_rate: Conversion rate used                          │
    │   - receipt_type: ADVANCE, FINAL, etc.                           │
    │   - stage: Which step (before/after agreement)                   │
    │   - created_at: When recorded                                    │
    │                                                                   │
    │ CURRENT ADVANCES IN DATABASE:                                    │
    """)
    
    result = db.session.execute(text("""
        SELECT id, customer, batch_id, amount_rwf, currency, receipt_type, created_at
        FROM customer_receipt
        WHERE receipt_type = 'ADVANCE'
        ORDER BY created_at DESC
        LIMIT 5;
    """)).fetchall()
    
    if result:
        for i, row in enumerate(result, 1):
            print(f"\n    {i}. Receipt #{row[0]}: customer='{row[1]}', batch={row[2]}, amount={row[3]:.2f} {row[4]}, recorded={row[6]}")
    else:
        print("\n    → NO ADVANCES RECORDED YET")
    
    print("""
    │                                                                   │
    └──────────────────────────────────────────────────────────────────┘
    """)
    
    print("\n\n" + "█" * 110)
    print("█ STEP 5: LATER - NEGOTIATOR RETURNS TO ENTER AGREED AMOUNT + DEDUCTIONS")
    print("█" * 110)
    print("""
    This goes back to customer_receipts page and enters:
      - total_expected_amount: The agreed final price
      - Deduction amounts: RMA, Transport, Alex Fee, Percentage
    
    This creates a new entry in customer_receipt with receipt_type='FINAL'
    And stores deductions in batch_deduction table
    
    FINAL RESULT IN CUSTOMER LEDGER:
      - Previous ADVANCE: +1000000 RWF
      - Agreement: +5000000 RWF total
      - Deductions: -500000 RWF (example)
      - Outstanding to pay: 5000000 - 1000000 - 500000 = 3500000 RWF
    """)
    
    print("\n\n" + "=" * 110)
    print("KEY TAKEAWAYS & TROUBLESHOOTING")
    print("=" * 110)
    print("""
    WHY IS THE ERROR APPEARING?
    ✗ Error: "No agreed total entered yet. Use Advance Only now..."
    ✓ Solution: When entering customer WITHOUT amounts, MUST check "Advance Only" checkbox
    
    WHY ISN'T CUSTOMER APPEARING IN UPDATE DEBTS?
    ✗ Problem: Customer not in dropdown
    ✓ Cause 1: BulkOutputPlan.customer is still NULL
      → Verify negotiator actually entered & saved the customer name
    ✓ Cause 2: Plan status is not STOCK_CONFIRMED or EXECUTED
      → Check plan.status in bulk_output_plan table
    ✓ Cause 3: Mineral type is COLTAN (negotiator only sees copper/cassiterite)
      → Check plan.mineral_type
    
    DATABASE CHECK QUERIES:
    """)
    
    print("\n\n1. Is your plan visible in database?")
    print("   SELECT id, batch_id, customer, status, mineral_type FROM bulk_output_plan LIMIT 5;")
    
    print("\n2. Does customer have NULL value?")
    print("   SELECT id, batch_id, customer FROM bulk_output_plan WHERE customer IS NULL LIMIT 5;")
    
    print("\n3. What advances have been recorded?")
    print("   SELECT id, customer, batch_id, amount_rwf, receipt_type FROM customer_receipt LIMIT 5;")
    
    print("\n4. What batch deductions exist?")
    print("   SELECT id, batch_id, deduction_type, amount_rwf FROM batch_deduction LIMIT 5;")
    
    print("\n\n" + "=" * 110)

if __name__ == '__main__':
    pass
