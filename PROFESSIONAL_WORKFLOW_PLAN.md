# Professional Negotiator Workflow - Implementation Plan

## Issues to Fix

### 1. Database Type Error
**Error:** `invalid input syntax for type integer: "batch_20260518_37e140"`
**Root Cause:** Old batch_deduction records have string batch_id instead of integer plan.id
**Solution:** Create migration to clean data + code to prevent future issues

### 2. Deduction Currency Handling  
**Requirement:** Deductions must have BOTH USD and RWF equivalents
- User enters amount in USD
- System calculates RWF using exchange rate
- Both stored for auditable history
- Ledger shows both currencies

**Current Structure:** BatchDeduction already has currency/exchange_rate columns!
**Missing:** UI to select currency and calculate both amounts

### 3. Agreement Overwriting
**Requirement:** Prevent overwriting agreements, create audit trail instead
**Current Issue:** When negotiator modifies an agreement, it overwrites old data
**Solution:** 
- Create AgreementVersion table to track all changes
- Show version history to user
- Each change creates new version with timestamps
- Old versions retained for audit

### 4. Advance Recording  
**Requirement:** When negotiator records ADVANCE, should:
- Create CustomerUnearnedReceipt (done)
- Link to potential debt/agreement
- Show in ledger with running balance
- No contradictions between advance and debt
- Async handling for reliability

**Current Structure:** 
- CustomerUnearnedReceipt exists
- CustomerUnearnedAllocation exists
- Ledger query added in previous fix
- Need: Better linking and visibility

## Database Schema Changes Needed

### 1. Fix BatchDeduction batch_id Type
Migrate: Convert string batch_id to integer where possible, delete corrupted records

### 2. Add AgreementVersion Table
```sql
CREATE TABLE agreement_version (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER FK,
    version_number INTEGER,
    agreed_amount NUMERIC,
    currency VARCHAR,
    total_deductions NUMERIC,
    net_amount NUMERIC,
    status VARCHAR (ACTIVE/SUPERSEDED),
    created_by_id INTEGER FK,
    created_at TIMESTAMP,
    reason_for_change VARCHAR
);
```

### 3. Enhance BatchDeduction 
Already has currency/exchange_rate! Just need UI fix.

## Workflow Changes

### Current Negotiator Workflow (PROBLEMATIC)
```
1. Select batch
2. Enter customer + agreed amount
3. Click "Save Agreement"
   → Creates/OVERWRITES BulkOutputPlan
   → Creates PaymentReview for boss
4. Boss approves
5. Boss adds deductions (doesn't know what negotiator saw)
6. Ledger shows result
```

**Problems:**
- No visibility into negotiator decisions
- Agreement can be accidentally overwritten
- Deductions added later by boss, not negotiator's input
- No clear audit trail
- Advance and agreement completely separate workflows

### New Professional Workflow (PROPOSED)
```
1. Select batch
2. Option A: Record ADVANCE
   ├─ Enter customer + advance amount (USD)
   ├─ System converts to RWF
   ├─ Creates CustomerUnearnedReceipt
   ├─ Advance visible in ledger
   └─ → Can edit later

3. Option B: Record AGREEMENT
   ├─ Select customer
   ├─ Enter agreed amount (USD)
   ├─ Enter estimated deductions:
   │  ├─ Transport (USD) + Exchange Rate
   │  ├─ RMA (USD) + Exchange Rate  
   │  └─ Other (USD) + Exchange Rate
   ├─ System calculates:
   │  ├─ Agreed amount in RWF
   │  ├─ Each deduction in RWF
   │  ├─ Net amount = Agreed - Deductions
   │  └─ Running balance = Net - (Advances already paid)
   ├─ Show preview with all currency equivalents
   ├─ Submit creates:
   │  ├─ AgreementVersion 1 (ACTIVE)
   │  ├─ BatchDeduction rows (with USD + RWF)
   │  ├─ PaymentReview for boss (with negotiator's deduction data)
   │  └─ Audit log entry
   ├─ Ledger immediately updates showing:
   │  ├─ ADVANCE (if recorded earlier)
   │  ├─ AGREEMENT (with negotiator deductions)
   │  ├─ Deductions itemized
   │  └─ Running balance = Customer owes this much NOW

4. Later: Modify Agreement (creates new version)
   ├─ If customer negotiates price down:
   │  ├─ Can revise agreed amount
   │  ├─ Creates AgreementVersion 2 (ACTIVE)
   │  ├─ Marks Version 1 as SUPERSEDED
   │  ├─ Shows "Modified by negotiator at X date, reason: customer renegotiation"
   │  ├─ New PaymentReview sent to boss
   │  └─ Old version retained for audit

5. Boss Reviews
   ├─ Sees AgreementVersion ACTIVE
   ├─ Sees negotiator's deductions (USD + RWF)
   ├─ Can approve OR request changes  
   ├─ Cannot modify - must tell negotiator to revise
   └─ Once approved: Locks agreement

6. Customer Ledger Shows
   ├─ Date | Entry | Debit (Owed) | Credit (Paid) | Balance
   ├─ 2026-05-20 | ADVANCE (Pending) | - | 100,000 RWF | 100,000 RWF
   ├─ 2026-05-21 | AGREEMENT (USD 1000 @ 1200) | 1,200,000 RWF | - | 1,300,000 RWF
   ├─ 2026-05-21 | DEDUCTION: Transport (USD 50 @ 1200) | - | 60,000 RWF | 1,240,000 RWF
   ├─ 2026-05-21 | DEDUCTION: RMA (USD 30 @ 1200) | - | 36,000 RWF | 1,204,000 RWF
   ├─ 2026-05-22 | SETTLEMENT (Customer paid 500K RWF) | - | 500,000 RWF | 704,000 RWF
   ├─ 2026-05-25 | ADVANCE APPLIED (100K → Batch) | - | 100,000 RWF | 604,000 RWF
   └─ Running Balance: Customer owes us 604,000 RWF
```

## Implementation Priority

### Phase 1: Fix Critical Issues
1. Clean batch_deduction records (migrate batch_id to integers)
2. Add type checking in code to prevent future issues
3. Fix deduction UI to handle USD + RWF

### Phase 2: Audit Trail
1. Create AgreementVersion table
2. Update customer_receipts route to create versions instead of overwriting
3. Show version history in UI

### Phase 3: Professional Ledger  
1. Enhance ledger query to show all entry types properly sorted
2. Add currency display for each transaction
3. Show running balance clearly
4. Add filtering by date/type

### Phase 4: UX Polish
1. Show deduction preview before submitting
2. Show ledger immediately after recording
3. Add confirmation dialogs for important actions
4. Better error handling for currency conversions

## Key Benefits

✓ **Auditable:** Every change tracked with versions
✓ **Professional:** Deductions handled with proper currency
✓ **Clear:** Ledger shows exactly what customer owes
✓ **Safe:** Can't accidentally overwrite agreements
✓ **Transparent:** Boss sees all negotiator decisions
✓ **Consistent:** Advances and settlements visible together
✓ **Currency-aware:** USD + RWF displayed side-by-side

