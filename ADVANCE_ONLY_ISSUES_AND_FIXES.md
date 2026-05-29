# ADVANCE-ONLY WORKFLOW - THREE CRITICAL ISSUES

## Problem 1: Boss Reviews ZERO Total Amount (NOT the advance amount)

**Where:** `core/routes/management.py` Line 3560-3625 (customer_receipts route for advance_only action)

**Current Behavior:**
```python
# Line 3627 - Advance-only creates CustomerUnearnedReceipt
unearned = CustomerUnearnedReceipt(
    amount_input=float(adv_amount_input),      # ← Advance amount is stored
    amount_rwf=float(adv_amount_rwf),          # ← Stored correctly
    remaining_rwf=0.0,
    note=adv_note or f'Advance recorded from agreement page for batch {plan.batch_id}',
)
```

**Problem:** Advance-only flow does NOT create a `PaymentReview` record!  
- The advance just goes to `CustomerUnearnedReceipt` table
- No entry in `PaymentReview` table for boss approval
- Boss CANNOT see this advance to approve/review it
- **Later, when negotiator enters the agreement, the agreement itself** (total_expected_amount=0 initially) **gets sent to boss for approval, NOT the advance**

**Result:** Boss dashboard shows total_expected_amount = 0 (because no agreement submitted yet)

---

## Problem 2: Advance NOT Visible in Customer Ledger (Shows Zero)

**Where:** `core/routes/management.py` Line 4469-4750 (_customer_ledger_data function)

**Current Behavior - Query includes:**
```python
# Line 4605-4630: Plans query (debits)
plans_stmt = (
    select(..., BulkOutputPlan.total_expected_amount.label('debit'), ...)
    .where(
        BulkOutputPlan.customer == customer_name,
        BulkOutputPlan.mineral_type.in_(aliases),
    )
)

# Line 4646-4661: Allocations query (credits from unearned allocations)
allocations_stmt = (
    select(..., CustomerUnearnedAllocation.applied_amount_rwf.label('credit_rwf'), ...)
    .select_from(CustomerUnearnedAllocation.__table__.join(CustomerUnearnedReceipt, ...))
    .where(
        CustomerUnearnedReceipt.customer == customer_name,
        CustomerUnearnedAllocation.stock_mineral_type.in_(aliases),
    )
)
```

**Problem:** The query shows `CustomerUnearnedAllocation.applied_amount_rwf` (allocated to a batch), BUT:
1. When user records advance-only, NO batch_id or plan_id is specified initially
2. CustomerUnearnedAllocation needs `batch_id` to join with a plan
3. **If the advance is recorded without allocating to a batch**, it won't appear in the ledger!

**Evidence - Line 3610-3625 (advance-only creation):**
```python
alloc = CustomerUnearnedAllocation(
    unearned_id=int(unearned.id),
    batch_id=plan.batch_id,  # ← Batch IS specified here
    stock_mineral_type=_canonical_mineral_type(plan.mineral_type) or plan.mineral_type,
    applied_amount_rwf=float(adv_amount_rwf),
)
```

**Wait - batch_id IS being set!** So the real problem is: 

**ACTUAL PROBLEM:** The ledger query needs to also include `CustomerUnearnedReceipt` records directly (not just allocations), because unearned receipts can exist WITHOUT being allocated to a batch yet!

---

## Problem 3: Workflow Logic Gap - Where Should Advances Go?

There are TWO conflicting workflows implemented:

### Current Flow (Broken):
1. Negotiator checks "advance only" ✓
2. Advance recorded to `CustomerUnearnedReceipt` ✓
3. Allocated to `CustomerUnearnedAllocation` ✓
4. **NO** PaymentReview sent to boss ✗
5. Ledger shows allocation if batch_id matches ✗ (sometimes fails)
6. Later, negotiator enters agreement → PaymentReview sent to boss with total_expected_amount

### Alternative Flow (What user expects):
1. Negotiator checks "advance only" + enters advance amount
2. System creates `PaymentReview` for boss approval (not just unearned record)
3. Boss sees advance in payment review and approves
4. Advance becomes visible in customer ledger
5. Later, negotiator enters agreement + deductions

---

## ROOT CAUSE ANALYSIS

**The code assumes:**
- Advance-only is a TEMPORARY state (customer_unearned_receipt)
- It will be allocated later
- Final approval happens when agreement is entered

**What actually happens:**
- User records advance, expects to see it immediately
- Boss doesn't see anything to approve
- Ledger doesn't show it (or shows zero)
- User confused about whether advance was recorded

---

## REQUIRED FIXES

### Fix 1: Create PaymentReview for Advance-Only Mode
**Location:** `core/routes/management.py` Line 3618-3625

**Change:** After creating `CustomerUnearnedReceipt` and `CustomerUnearnedAllocation`, create a `PaymentReview` entry so boss can review/approve

```python
# After line 3625, before db.commit():
review = PaymentReview(
    type='customer_advance',                    # New type for advances
    customer=submitted_customer,
    amount=float(adv_amount_input),
    currency=adv_currency,
    created_by_id=getattr(current_user, 'id', None),
    status=PaymentReviewStatus.PENDING_REVIEW.value,
    request_payload=json.dumps({
        'action': 'customer_advance',
        'customer': submitted_customer,
        'batch_id': plan.batch_id,
        'mineral_type': plan.mineral_type,
        'amount': float(adv_amount_input),
        'currency': adv_currency,
        'exchange_rate': float(adv_exchange_rate or 1.0),
        'unearned_id': int(unearned.id),
        'payment_channel': adv_channel,
    }),
)
db.session.add(review)
```

### Fix 2: Include Unearned Receipts Directly in Ledger Query
**Location:** `core/routes/management.py` Line 4600-4750 (_customer_ledger_data)

**Add new query:** Include `CustomerUnearnedReceipt` records that haven't been fully allocated yet

```python
# Add after allocations_stmt (line 4661):
unearned_stmt = (
    select(
        CustomerUnearnedReceipt.created_at.label('date'),
        literal(5).label('sort_key'),                           # After allocations
        literal('unearned').label('entry_kind'),
        typed_int_null.label('plan_id'),
        CustomerUnearnedReceipt.id.label('receipt_id'),
        literal(None).label('batch_id'),                        # No batch yet
        literal(0.0).label('debit'),
        CustomerUnearnedReceipt.amount_input.label('credit'),
        CustomerUnearnedReceipt.amount_input.label('original_amount'),
        CustomerUnearnedReceipt.currency.label('original_currency'),
        CustomerUnearnedReceipt.exchange_rate.label('original_exchange_rate'),
        literal(0.0).label('debit_rwf'),
        func.coalesce(CustomerUnearnedReceipt.amount_rwf, 0).label('credit_rwf'),
        literal(None).label('proof_path'),
        literal('ADVANCE').label('detail'),
    )
    .where(
        CustomerUnearnedReceipt.customer == customer_name,
        CustomerUnearnedReceipt.currency.in_(aliases),  # Filter by mineral type stored in currency?
    )
)
# Then update union_all:
ledger_union = union_all(plans_stmt, deductions_stmt, receipts_stmt, allocations_stmt, unearned_stmt).subquery('ledger_union')
```

### Fix 3: Update Ledger Template to Show "ADVANCE" Type
**Location:** `templates/negotiator/customer_ledger.html`

**Change:** Display entry_kind=`unearned` as "ADVANCE (Pending Allocation)" in the ledger table

---

## TESTING SCENARIO

After fixes applied:

1. **Login as Negotiator**
2. **Go to Record Customer Receipts**
3. **Select batch, customer, check "Advance Only"**
4. **Enter: 100,000 RWF (or 100 USD)**
5. **Submit**

**Expected result:**
- ✓ Advance recorded message shown
- ✓ Navigate to Customer Ledgers → see advance listed (not zero!)
- ✓ Boss sees payment review: "Customer advance: 100,000 RWF for [customer]"
- ✓ Boss approves
- ✓ Later: Negotiator returns, uncheck "Advance Only", enter agreement amount
- ✓ Agreement goes to boss for approval
- ✓ Final ledger shows: Advance + Agreement - Deductions = Outstanding

---

## SQL QUERIES TO VERIFY CURRENT STATE

Check if advances are being recorded:
```sql
SELECT id, customer, amount_input, currency, amount_rwf, created_at 
FROM customer_unearned_receipt 
LIMIT 5;
```

Check if allocations exist:
```sql
SELECT id, unearned_id, batch_id, applied_amount_rwf, created_at 
FROM customer_unearned_allocation 
LIMIT 5;
```

Check if boss sees them in payment review:
```sql
SELECT id, type, customer, amount, currency, status, created_at 
FROM payment_review 
WHERE type = 'customer_advance' OR customer IS NOT NULL
LIMIT 5;
```

Expected: Advance records in unearned_receipt, allocations, but NO payment_review (this is the bug!)
