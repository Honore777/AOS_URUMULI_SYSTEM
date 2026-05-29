# Table Relationships & Data Flow Diagram

## 📊 Complete Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TRANSPORTER ADVANCE FLOW                            │
└─────────────────────────────────────────────────────────────────────────────┘

STEP 1: Boss Approves Transporter Advance
═══════════════════════════════════════════════════════════════════════════════

   unified_supplier_advance
   ┌─────────────────────────┐
   │ id: 1                   │
   │ transporter_name: Kasungu Ltd │
   │ amount_rwf: 1,000,000   │  ← USD 1000 at rate 1000
   │ currency: USD           │
   │ exchange_rate: 1000.0   │
   └────────┬────────────────┘
            │
            │ (used to pay for stocks)
            │
            ├──────────────────┬──────────────────┐
            │                  │                  │
            ▼                  ▼                  ▼
   (135,000 RWF used) (135,000 RWF used)


STEP 2: Transporter Brings Stock from Two Suppliers
═══════════════════════════════════════════════════════════════════════════════

   cassiterite_stock          cassiterite_stock
   ┌────────────────────────┐  ┌────────────────────────┐
   │ id: 1                  │  │ id: 2                  │
   │ transporter_name:      │  │ transporter_name:      │
   │   Kasungu Ltd          │  │   Kasungu Ltd          │
   │ supplier: John Mwale   │  │ supplier: Mama Charity │
   │ quantity: 750 kg       │  │ quantity: 500 kg       │
   │ price_per_kg: 100      │  │ price_per_kg: 120      │
   │ balance_to_pay: 75,000 │  │ balance_to_pay: 60,000 │
   └────────┬────────────────┘  └────────┬────────────────┘
            │                            │
            │ (advance covers both)      │
            │                            │
            └────────┬───────────────────┘
                     │
                     ▼
        cassiterite_advance_allocation
        ┌──────────────────────────────────┐
        │ id: 1                            │
        │ stock_id: 1                      │
        │ advance_id: 1                    │
        │ applied_amount: 75,000 RWF       │
        └──────────────────────────────────┘
        
        cassiterite_advance_allocation
        ┌──────────────────────────────────┐
        │ id: 2                            │
        │ stock_id: 2                      │
        │ advance_id: 1                    │
        │ applied_amount: 60,000 RWF       │
        └──────────────────────────────────┘


STEP 3: Charge Business Retention Fees to Suppliers
═══════════════════════════════════════════════════════════════════════════════

   supplier_deduction              supplier_deduction
   ┌────────────────────┐          ┌────────────────────┐
   │ id: 1              │          │ id: 2              │
   │ supplier_name:     │          │ supplier_name:     │
   │   John Mwale       │          │   Mama Charity     │
   │ deduction_type:    │          │ deduction_type:    │
   │   BUSINESS_...     │          │   BUSINESS_...     │
   │ amount_rwf: 100    │          │ amount_rwf: 100    │
   └────────┬───────────┘          └────────┬───────────┘
            │                               │
            │ (linked to transporter ledger)
            │                               │
            └────────┬─────────────────────┘
                     │
                     ▼
            transporter_ledger
        ┌──────────────────────────────────┐
        │ id: 2                            │
        │ transporter_name: Kasungu Ltd    │
        │ supplier_name: John Mwale        │
        │ entry_type:                      │
        │   BUSINESS_RETENTION_RECOVERY    │
        │ amount_rwf: -100 RWF             │
        │ source_supplier_deduction_id: 1  │◄─── LINKS BACK TO FEE
        └──────────────────────────────────┘
        
            transporter_ledger
        ┌──────────────────────────────────┐
        │ id: 3                            │
        │ transporter_name: Kasungu Ltd    │
        │ supplier_name: Mama Charity      │
        │ entry_type:                      │
        │   BUSINESS_RETENTION_RECOVERY    │
        │ amount_rwf: -100 RWF             │
        │ source_supplier_deduction_id: 2  │◄─── LINKS BACK TO FEE
        └──────────────────────────────────┘


STEP 4: Calculate Supplier Balances (Includes Fee Deduction)
═══════════════════════════════════════════════════════════════════════════════

   For John Mwale:
   ┌─────────────────────────────────────────┐
   │ cassiterite_stock.balance_to_pay: 75k   │
   │ cassiterite_advance_allocation: -75k    │
   │ supplier_deduction.amount_rwf: -100     │
   ├─────────────────────────────────────────┤
   │ NET BALANCE: 75k - 75k - 100 = -100     │  ✓ Supplier has credit
   └─────────────────────────────────────────┘

   For Mama Charity:
   ┌─────────────────────────────────────────┐
   │ cassiterite_stock.balance_to_pay: 60k   │
   │ cassiterite_advance_allocation: -60k    │
   │ supplier_deduction.amount_rwf: -100     │
   ├─────────────────────────────────────────┤
   │ NET BALANCE: 60k - 60k - 100 = -100     │  ✓ Supplier has credit
   └─────────────────────────────────────────┘


STEP 5: Request Transporter Settlement Payment
═══════════════════════════════════════════════════════════════════════════════

   transporter_ledger (all entries for Kasungu Ltd)
   ┌────────────────────────────────────────┐
   │ entry_type: ADVANCE                    │
   │ amount_rwf: 1,000,000                  │
   └────────────────────────────────────────┘
   
   ┌────────────────────────────────────────┐
   │ entry_type: BUSINESS_RETENTION_RECOVERY│
   │ amount_rwf: -100 (John fee)             │
   └────────────────────────────────────────┘
   
   ┌────────────────────────────────────────┐
   │ entry_type: BUSINESS_RETENTION_RECOVERY│
   │ amount_rwf: -100 (Mama fee)             │
   └────────────────────────────────────────┘
   
   SUM = 1,000,000 - 100 - 100 = 999,800 RWF
   
                    │
                    │ (Request settlement with this balance)
                    ▼
           payment_review
        ┌───────────────────────────────────────┐
        │ id: 1                                 │
        │ type: transporter_payment             │
        │ customer: Kasungu Ltd                 │
        │ amount: 999,800 RWF                   │  ◄── Fee already deducted!
        │ status: PENDING_REVIEW                │
        │ request_payload:                      │
        │   {action: pay_transporter,           │
        │    amount_rwf: 999800}                │
        └───────────────────────────────────────┘


STEP 6: Boss Approves & Cashier Disburses Payment
═══════════════════════════════════════════════════════════════════════════════

   payment_review(1)
   ┌──────────────────────────┐
   │ status: APPROVED         │
   └────────┬─────────────────┘
            │ (cashier processes this)
            │
            ├─────────────────┬─────────────────┐
            │                 │                 │
            ▼                 ▼                 ▼
   
   cash_account          cash_transaction      transporter_ledger
   ┌──────────────┐      ┌──────────────────┐  ┌──────────────────────┐
   │ id: 1        │      │ id: 1            │  │ id: 4                │
   │ current_bal: │      │ account_id: 1    │  │ transporter_name:    │
   │ 2,000,000 RWF│ ───► │ amount_rwf:      │  │   Kasungu Ltd        │
   │              │      │   999,800        │  │ entry_type:          │
   │ (BEFORE: 2M) │      │ direction: OUT   │  │   CASH_PAYMENT       │
   │ (AFTER: 1M)  │      │ reference:       │  │ amount_rwf: -999,800 │
   └──────────────┘      │  transporter_... │  │ is_paid: TRUE        │
                         └──────┬───────────┘  │ payment_review_id: 1 │
                                │              │ cash_transaction_id:1│
                                └──────────────┤ (LINKS BACK)         │
                                               └──────────────────────┘

   payment_review(1) UPDATED:
   ┌─────────────────────────────────────┐
   │ cash_transaction_id: 1              │ ◄── LINKED
   │ cash_account_id: 1                  │ ◄── LINKED
   │ disbursement_status: DISBURSED      │
   └─────────────────────────────────────┘


FINAL STATE: Complete Ledger Reconciliation
═══════════════════════════════════════════════════════════════════════════════

   transporter_ledger (sum for Kasungu Ltd):
   ┌────────────────────────────────────────┐
   │ ADVANCE:                  +1,000,000    │
   │ BUSINESS_RETENTION (John):    -100     │
   │ BUSINESS_RETENTION (Mama):    -100     │
   │ CASH_PAYMENT:               -999,800    │
   ├────────────────────────────────────────┤
   │ TOTAL BALANCE:                  0.0    │  ✓ FULLY SETTLED
   └────────────────────────────────────────┘

   supplier_ledger (John Mwale):
   ┌────────────────────────────────────────┐
   │ Stock debt:                 75,000      │
   │ Advance applied:           -75,000      │
   │ Business retention fee:       -100      │
   ├────────────────────────────────────────┤
   │ TOTAL BALANCE:               -100       │  ✓ CREDIT (supplier overpaid)
   └────────────────────────────────────────┘

   supplier_ledger (Mama Charity):
   ┌────────────────────────────────────────┐
   │ Stock debt:                 60,000      │
   │ Advance applied:           -60,000      │
   │ Business retention fee:       -100      │
   ├────────────────────────────────────────┤
   │ TOTAL BALANCE:               -100       │  ✓ CREDIT (supplier overpaid)
   └────────────────────────────────────────┘

   cash_account:
   ┌────────────────────────────────────────┐
   │ Opening balance:        2,000,000       │
   │ Payment to transporter:   -999,800      │
   ├────────────────────────────────────────┤
   │ CLOSING BALANCE:        1,000,200       │  ✓ PROPERLY DEBITED
   └────────────────────────────────────────┘

   unified_supplier_advance (Kasungu Ltd):
   ┌────────────────────────────────────────┐
   │ Original advance:       1,000,000       │
   │ Applied to John:          -75,000       │
   │ Applied to Mama:          -60,000       │
   ├────────────────────────────────────────┤
   │ REMAINING BALANCE:        865,000       │  ✓ TRACKED
   └────────────────────────────────────────┘
```

---

## 📋 Foreign Key Linkages (The Glue)

```
unified_supplier_advance(1)
    ▲
    │ provides funds to
    │
cassiterite_advance_allocation(1,2)
    │ apply to
    ▼
cassiterite_stock(1,2)
    │ creates debt for
    ▼
supplier_deduction(1,2) ◄─ Business retention fees
    │ linked to
    ▼
transporter_ledger(2,3) ◄─ BUSINESS_RETENTION_RECOVERY entries
    │ with source_supplier_deduction_id
    │
transporter_ledger(1) ◄────────── ADVANCE entry (original)
    │ sums to
    │ (1,000,000 - 100 - 100 - 999,800 = 0)
    │
transporter_ledger(4) ◄────────── CASH_PAYMENT entry (signed)
    │ linked to
    ├─ payment_review_id(1)
    │
    └─ cash_transaction_id(1)
        │ debits
        ▼
        cash_account(1)
        
payment_review(1)
    ├─ links to cash_transaction(1)
    ├─ links to cash_account(1)
    ├─ links to transporter_ledger(4)
    └─ has request_payload with all details
```

---

## 🔍 Key Verification Points

### 1. Data Integrity
- ✓ All source_supplier_deduction_id point to valid supplier_deduction records
- ✓ All payment_review_id point to valid payment_review records
- ✓ All cash_transaction_id point to valid cash_transaction records

### 2. Balance Integrity
- ✓ Transporter ledger sum = 0 (no money lost)
- ✓ Supplier balance = stock_debt - advance_allocated - fees
- ✓ Cash account = previous_balance - payment_amount

### 3. Amount Integrity
- ✓ payment_review.amount = SUM(transporter_ledger) AFTER fee deduction
- ✓ cassiterite_advance_allocation.applied_amount = cassiterite_stock.balance_to_pay
- ✓ cash_transaction.amount_rwf = payment_review.amount

### 4. Audit Trail
- ✓ source_supplier_deduction_id allows tracing fees back to original entry
- ✓ payment_review_id allows tracing payment back to approval
- ✓ cash_transaction_id allows tracing payment to cash movement
- ✓ created_by_id tracks who made each entry
- ✓ created_at timestamps show chronological order
