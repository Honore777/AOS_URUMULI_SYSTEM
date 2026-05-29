# Simplified Professional Workflow - What's Needed

## ✓ Fixed Issue
**TypeError: '<' not supported between str and int** 
- Root cause: BatchDeduction.batch_id is INTEGER (FK to plan.id), but we were mixing it with string batch_ids
- Fix: Use `d.plan.batch_id` (the string) instead of `d.batch_id` (the integer)
- Status: ✓ FIXED in management.py line 4405

---

## What Users Actually Need (NOT Complex)

### For Negotiator (When Recording Agreement)

**Current Form Should:**
1. ✓ Already has: Agreed amount input
2. ✓ Already has: Currency selector (RWF/USD)
3. ✓ Already has: Exchange rate field
4. **Need: Deduction section where negotiator enters estimates**
   - Transport Cost (USD)
   - RMA Cost (USD)
   - Other (USD)
   - Each with exchange rate field
   - System calculates RWF amount automatically
   - Shows total deductions preview

**Form Preview Should Show:**
```
AGREEMENT SUMMARY
Agreed Amount: USD 1,000
Exchange Rate: 1,200
Agreed in RWF: 1,200,000

ESTIMATED DEDUCTIONS
Transport: USD 50 @ 1,200 = 60,000 RWF
RMA: USD 30 @ 1,200 = 36,000 RWF
Total Deductions: 96,000 RWF

NET AMOUNT (What Customer Owes): 1,104,000 RWF
```

### For Boss (Payment Review)

**Boss Should See:**
```
AGREEMENT TO REVIEW
Customer: John Doe
Agreed by: Negotiator (date)
Status: Pending Approval

AGREEMENT DETAILS
Amount: USD 1,000 @ 1,200 = 1,200,000 RWF

DEDUCTIONS (As proposed by Negotiator)
- Transport: USD 50 @ 1,200 = 60,000 RWF
- RMA: USD 30 @ 1,200 = 36,000 RWF
Total: 96,000 RWF

NET AMOUNT CUSTOMER OWES: 1,104,000 RWF

[APPROVE] [REQUEST CHANGES] [REJECT]
```

### For Customer Ledger (What They Owe)

**Ledger Should Show:**
```
Date       Entry                           Debit      Credit     Running Balance
2026-05-20 ADVANCE (Pending)                          100,000    100,000
2026-05-21 AGREEMENT (USD 1,000)         1,200,000             1,300,000
2026-05-21 - Transport Deduction            60,000             1,240,000
2026-05-21 - RMA Deduction                  36,000             1,204,000
2026-05-22 CUSTOMER PAYMENT              (500,000)            704,000
2026-05-25 ADVANCE APPLIED TO BATCH                 100,000    604,000

CUSTOMER OWES: 604,000 RWF
```

---

## What's Already There (Don't Need New Models)

✓ BatchDeduction table - already stores currency, exchange_rate, amount_rwf
✓ PaymentReview table - already stores payment data
✓ CustomerUnearnedReceipt - already stores advances
✓ Ledger queries - already formatted to show all entries

## What We Actually Need to Do

### 1. ✓ DONE
Fix the batch_id type error (string vs integer mix)

### 2. UI/Form Changes (Simple)
- Add deduction input fields to customer_receipts.html
- Each deduction: USD amount + Exchange rate
- Show preview before submit

### 3. Boss Review Template Change (Simple)
- Display deductions with both USD and RWF
- Clear "NET AMOUNT" display
- Show what negotiator entered (not what boss modified)

### 4. Ledger Display (Already Works)
- Deductions already show with amount_rwf
- Just need to ensure currency is displayed
- Running balance already calculated correctly

---

## Database: No Changes Needed

❌ Don't create AgreementVersion table (too complex)
❌ Don't add new fields to BatchDeduction (already has what we need)
✓ Just use what's there: currency, exchange_rate, amount_rwf

---

## Audit Trail: Simple Approach

**Instead of Version Table:**
- Keep created_at timestamp (already there)
- Keep created_by_id (already there)
- Ledger shows: "Agreement recorded by [negotiator] on [date]"
- Boss review shows: "Proposed by [negotiator] on [date]"
- No overwriting - each new submission is a new agreement (if customer renegotiates)

**In Ledger:**
- Each transaction is visible with date and who created it
- User can see the sequence: Advance → Agreement → Deductions → Payments
- Complete audit trail without version table

---

## Summary

✓ **Database:** Use what exists (no new tables)
✓ **Audit:** Use timestamps + created_by + ledger sequence  
✓ **Clarity:** Better UI display of USD + RWF
✓ **Simple:** No complex versioning logic
✓ **Professional:** Clear, auditable, understandable

The workflow is already auditable through the ledger. We just need to make the deductions visible with both currencies, and fix the UI to let negotiators enter them properly.

