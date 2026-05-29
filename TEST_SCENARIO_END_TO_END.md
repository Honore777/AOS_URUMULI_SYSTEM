# Complete Test Scenario: End-to-End Workflow

## 🎯 Test Scenario Overview

### Business Flow
1. **Boss approves transporter "Kasungu Ltd" for 1000 USD advance** (~1,000,000 RWF at rate 1000)
2. **Kasungu brings cassiterite from two suppliers**:
   - Supplier "John Mwale" (750 kg at 100 RWF/kg = 75,000 RWF)
   - Supplier "Mama Charity" (500 kg at 120 RWF/kg = 60,000 RWF)
3. **Record stocks in cassiterite module**
4. **Track advance allocation** to both suppliers
5. **Charge business retention fee** to both suppliers (100 RWF each)
6. **Verify supplier balances** reflect fees and advances
7. **Request transporter settlement** (should be advance minus fees)
8. **Disburse payment** and verify ledgers match

---

## 📊 All Tables Involved: Structure & Purpose

### 1️⃣ **unified_supplier_advance** - Tracks cash advances to transporters

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| transporter_name | VARCHAR | Who received the advance |
| supplier_name_norm | VARCHAR | Normalized for lookup |
| amount_rwf | DECIMAL | Amount in RWF |
| currency | VARCHAR | Original currency (USD/RWF) |
| exchange_rate | DECIMAL | Conversion rate used |
| amount_input | DECIMAL | Original input amount |
| is_deleted | BOOLEAN | Soft delete flag |
| created_at | TIMESTAMP | When advance was created |
| created_by_id | INT FK | Who created it |

**In our scenario:**
```sql
INSERT INTO unified_supplier_advance (
    transporter_name, supplier_name_norm, amount_rwf, currency, exchange_rate, amount_input
) VALUES (
    'Kasungu Ltd', 'kasungu ltd', 1000000.0, 'USD', 1000.0, 1000.0
);
-- Result: advance_id = 1, amount_rwf = 1,000,000.0
```

---

### 2️⃣ **cassiterite_stock** - Records minerals brought by transporter

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| transporter_name | VARCHAR | Who brought it |
| supplier | VARCHAR | Original supplier |
| quantity | DECIMAL | Weight in kg |
| price_per_kg | DECIMAL | Negotiated rate |
| balance_to_pay | DECIMAL | Amount owed to supplier |
| is_deleted | BOOLEAN | Soft delete |
| created_at | TIMESTAMP | When recorded |
| created_by_id | INT FK | Who recorded it |

**In our scenario - Stock 1:**
```sql
INSERT INTO cassiterite_stock (
    transporter_name, supplier, quantity, price_per_kg, balance_to_pay
) VALUES (
    'Kasungu Ltd', 'John Mwale', 750.0, 100.0, 75000.0
);
-- Result: stock1_id = 1, balance_to_pay = 75,000.0
```

**In our scenario - Stock 2:**
```sql
INSERT INTO cassiterite_stock (
    transporter_name, supplier, quantity, price_per_kg, balance_to_pay
) VALUES (
    'Kasungu Ltd', 'Mama Charity', 500.0, 120.0, 60000.0
);
-- Result: stock2_id = 2, balance_to_pay = 60,000.0
```

---

### 3️⃣ **cassiterite_advance_allocation** - Links advance to specific stock entries

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| stock_id | INT FK | Which stock this applies to |
| advance_id | INT FK | Which advance is being used |
| applied_amount | DECIMAL | How much of advance applied |
| created_at | TIMESTAMP | When allocated |

**In our scenario - Allocation 1:**
```sql
INSERT INTO cassiterite_advance_allocation (
    stock_id, advance_id, applied_amount
) VALUES (
    1, 1, 75000.0  -- Apply 75,000 RWF from advance to John's stock
);
-- Result: alloc1_id = 1
```

**In our scenario - Allocation 2:**
```sql
INSERT INTO cassiterite_advance_allocation (
    stock_id, advance_id, applied_amount
) VALUES (
    2, 1, 60000.0  -- Apply 60,000 RWF from advance to Mama's stock
);
-- Result: alloc2_id = 2
-- Total advance used: 135,000 RWF (leaving 865,000 balance)
```

---

### 4️⃣ **supplier_deduction** - Business retention fees charged to suppliers

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| supplier_name | VARCHAR | Supplier being charged |
| deduction_type | VARCHAR | Type: BUSINESS_RETENTION, etc. |
| amount_rwf | DECIMAL | Fee amount in RWF |
| created_at | TIMESTAMP | When fee was charged |

**In our scenario - Fee 1:**
```sql
INSERT INTO supplier_deduction (
    supplier_name, deduction_type, amount_rwf
) VALUES (
    'John Mwale', 'BUSINESS_RETENTION', 100.0
);
-- Result: fee1_id = 1
```

**In our scenario - Fee 2:**
```sql
INSERT INTO supplier_deduction (
    supplier_name, deduction_type, amount_rwf
) VALUES (
    'Mama Charity', 'BUSINESS_RETENTION', 100.0
);
-- Result: fee2_id = 2
```

---

### 5️⃣ **transporter_ledger** - Complete ledger of all transporter transactions

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| transporter_name | VARCHAR | Who transporter is |
| supplier_name | VARCHAR | Related supplier (if any) |
| entry_type | VARCHAR | ADVANCE, BUSINESS_RETENTION_RECOVERY, CASH_PAYMENT |
| amount_rwf | DECIMAL | Amount (positive = we owe, negative = deduction/payment) |
| source_supplier_deduction_id | INT FK | Links to supplier_deduction if fee |
| payment_review_id | INT FK | Links to payment approval |
| cash_transaction_id | INT FK | Links to cash transaction if paid |
| is_paid | BOOLEAN | Whether this was actually paid out |
| created_at | TIMESTAMP | When entry created |

**Entries created during scenario:**

**Entry 1 - Initial Advance (created when advance approved):**
```sql
INSERT INTO transporter_ledger (
    transporter_name, entry_type, amount_rwf
) VALUES (
    'Kasungu Ltd', 'ADVANCE', 1000000.0
);
-- ledger1_id = 1, amount = +1,000,000 (we owe transporter)
```

**Entry 2 - Business retention fee for John:**
```sql
INSERT INTO transporter_ledger (
    transporter_name, supplier_name, entry_type, amount_rwf, 
    source_supplier_deduction_id
) VALUES (
    'Kasungu Ltd', 'John Mwale', 'BUSINESS_RETENTION_RECOVERY', -100.0, 1
);
-- ledger2_id = 2, amount = -100 (reduces what we owe)
```

**Entry 3 - Business retention fee for Mama:**
```sql
INSERT INTO transporter_ledger (
    transporter_name, supplier_name, entry_type, amount_rwf, 
    source_supplier_deduction_id
) VALUES (
    'Kasungu Ltd', 'Mama Charity', 'BUSINESS_RETENTION_RECOVERY', -100.0, 2
);
-- ledger3_id = 3, amount = -100 (reduces what we owe)
```

---

### 6️⃣ **payment_review** - Tracks approval workflow for payments

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| type | VARCHAR | transporter_advance or transporter_payment |
| customer | VARCHAR | Transporter name |
| amount | DECIMAL | Amount being processed |
| status | VARCHAR | PENDING_REVIEW, APPROVED, DISBURSED |
| request_payload | JSON | Stores all transaction details |
| cash_transaction_id | INT FK | Links to actual cash movement |
| cash_account_id | INT FK | Which account paid from |
| created_at | TIMESTAMP | When request created |

**Entry - Payment Review (created when settlement requested):**
```sql
INSERT INTO payment_review (
    type, customer, amount, status, request_payload
) VALUES (
    'transporter_payment', 'Kasungu Ltd', 999800.0, 'PENDING_REVIEW',
    '{"action":"pay_transporter","transporter_name":"kasungu ltd","amount_rwf":999800.0}'
);
-- review_id = 1, amount = 999,800 (advance minus fees)
```

---

### 7️⃣ **cash_transaction** - Records actual cash movement

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| account_id | INT FK | Which cash account |
| amount_rwf | DECIMAL | Amount in RWF |
| direction | VARCHAR | IN or OUT |
| reference | VARCHAR | Description of transaction |
| created_at | TIMESTAMP | When paid |

**Entry - Cash Payment (created when cashier disburses):**
```sql
INSERT INTO cash_transaction (
    account_id, amount_rwf, direction, reference
) VALUES (
    1, 999800.0, 'OUT', 'transporter_payment:1'
);
-- tx_id = 1, reduces cash account balance by 999,800 RWF
```

---

### 8️⃣ **cash_account** - Cash/bank accounts

| Column | Type | Purpose |
|--------|------|---------|
| id | INT PK | Unique identifier |
| name | VARCHAR | Account name |
| current_balance | DECIMAL | Available cash |
| currency | VARCHAR | RWF/USD |

**Initial State (before payment):**
```sql
SELECT current_balance FROM cash_account WHERE id = 1;
-- Result: 2,000,000.0 RWF (initial balance)
```

**After Payment:**
```sql
-- Account balance is decreased by payment amount
UPDATE cash_account SET current_balance = 2000000.0 - 999800.0 = 1000200.0
WHERE id = 1;
```

---

## 🔄 Complete Ledger Verification Queries

### STEP 1: Verify Advance Created
```sql
SELECT * FROM unified_supplier_advance 
WHERE transporter_name = 'Kasungu Ltd' AND is_deleted = FALSE;

-- Expected result:
-- id | transporter_name | amount_rwf | currency | exchange_rate | amount_input
-- 1  | Kasungu Ltd      | 1000000.0  | USD      | 1000.0        | 1000.0
```

### STEP 2: Verify Stocks Recorded
```sql
SELECT id, supplier, quantity, price_per_kg, balance_to_pay 
FROM cassiterite_stock 
WHERE transporter_name = 'Kasungu Ltd' AND is_deleted = FALSE;

-- Expected result:
-- id | supplier      | quantity | price_per_kg | balance_to_pay
-- 1  | John Mwale    | 750.0    | 100.0        | 75000.0
-- 2  | Mama Charity  | 500.0    | 120.0        | 60000.0
```

### STEP 3: Verify Advance Allocations
```sql
SELECT ca.id, ca.stock_id, ca.advance_id, ca.applied_amount,
       cs.supplier, cs.balance_to_pay
FROM cassiterite_advance_allocation ca
JOIN cassiterite_stock cs ON cs.id = ca.stock_id
WHERE ca.advance_id = 1;

-- Expected result:
-- id | stock_id | advance_id | applied_amount | supplier     | balance_to_pay
-- 1  | 1        | 1          | 75000.0        | John Mwale   | 75000.0
-- 2  | 2        | 1          | 60000.0        | Mama Charity | 60000.0

-- Verification: Both stocks fully covered by advance (75k + 60k = 135k used from 1M)
```

### STEP 4: Verify Supplier Balances WITH Fees
```sql
-- For John Mwale:
-- Stock debt: 75,000 RWF
-- Advance allocation: -75,000 RWF
-- Business retention fee: -100 RWF
-- Net balance: 75000 - 75000 - 100 = -100 RWF (supplier has CREDIT)

-- For Mama Charity:
-- Stock debt: 60,000 RWF
-- Advance allocation: -60,000 RWF
-- Business retention fee: -100 RWF
-- Net balance: 60000 - 60000 - 100 = -100 RWF (supplier has CREDIT)

SELECT 
    'John Mwale' as supplier,
    (SELECT COALESCE(SUM(balance_to_pay), 0) 
     FROM cassiterite_stock 
     WHERE supplier ILIKE '%John Mwale%' AND is_deleted = FALSE) as stock_debt,
    (SELECT COALESCE(SUM(applied_amount), 0) 
     FROM cassiterite_advance_allocation ca
     JOIN cassiterite_stock cs ON cs.id = ca.stock_id
     WHERE cs.supplier ILIKE '%John Mwale%' AND cs.is_deleted = FALSE) as advance_applied,
    (SELECT COALESCE(SUM(amount_rwf), 0) 
     FROM supplier_deduction 
     WHERE supplier_name ILIKE '%John Mwale%') as fees_charged;

-- Expected: stock_debt=75000, advance_applied=75000, fees_charged=100
```

### STEP 5: Verify Transporter Ledger Entries
```sql
SELECT transporter_name, entry_type, amount_rwf, supplier_name,
       source_supplier_deduction_id, created_at
FROM transporter_ledger
WHERE transporter_name = 'Kasungu Ltd'
ORDER BY created_at;

-- Expected result (4 entries):
-- transporter_name | entry_type                    | amount_rwf | supplier_name
-- Kasungu Ltd      | ADVANCE                       | 1000000.0  | NULL
-- Kasungu Ltd      | BUSINESS_RETENTION_RECOVERY   | -100.0     | John Mwale
-- Kasungu Ltd      | BUSINESS_RETENTION_RECOVERY   | -100.0     | Mama Charity
-- Kasungu Ltd      | CASH_PAYMENT                  | -999800.0  | NULL

-- Sum verification: 1000000 - 100 - 100 - 999800 = 0 (balanced!)
```

### STEP 6: Calculate Transporter Settlement Amount
```sql
SELECT 
    transporter_name,
    SUM(amount_rwf) as balance_to_pay,
    COUNT(*) as entry_count,
    SUM(CASE WHEN entry_type = 'ADVANCE' THEN amount_rwf ELSE 0 END) as advances,
    SUM(CASE WHEN entry_type = 'BUSINESS_RETENTION_RECOVERY' THEN amount_rwf ELSE 0 END) as fees_deducted,
    SUM(CASE WHEN entry_type = 'CASH_PAYMENT' THEN amount_rwf ELSE 0 END) as paid
FROM transporter_ledger
WHERE transporter_name = 'Kasungu Ltd'
GROUP BY transporter_name;

-- Expected:
-- transporter_name | balance_to_pay | entry_count | advances | fees_deducted | paid
-- Kasungu Ltd      | 0.0           | 4           | 1000000  | -200.0        | -999800

-- This shows: We owe 1M, deducted 200 for fees, so paid 999,800
```

### STEP 7: Verify Payment Review Created
```sql
SELECT id, type, customer, amount, status
FROM payment_review
WHERE customer ILIKE '%Kasungu%' AND type = 'transporter_payment';

-- Expected:
-- id | type                | customer    | amount    | status
-- 1  | transporter_payment | Kasungu Ltd | 999800.0  | APPROVED (after boss approval)
```

### STEP 8: Verify Cash Transaction and Linkage
```sql
SELECT 
    pr.id as review_id,
    pr.customer,
    pr.amount as review_amount,
    ct.id as transaction_id,
    ct.amount_rwf as tx_amount,
    ct.direction,
    ca.current_balance,
    ca.id as account_id
FROM payment_review pr
LEFT JOIN cash_transaction ct ON pr.cash_transaction_id = ct.id
LEFT JOIN cash_account ca ON pr.cash_account_id = ca.id
WHERE pr.customer ILIKE '%Kasungu%';

-- Expected:
-- review_id | customer    | review_amount | transaction_id | tx_amount | direction | account_id
-- 1         | Kasungu Ltd | 999800.0      | 1              | 999800.0  | OUT       | 1

-- Account balance should have decreased: 2000000 - 999800 = 1000200
```

### STEP 9: Final Verification - All Ledgers Balance
```sql
-- TRANSPORTER LEDGER SUM (should be 0 - fully settled)
SELECT SUM(amount_rwf) as transporter_balance
FROM transporter_ledger
WHERE transporter_name = 'Kasungu Ltd';
-- Expected: 0.0 (all entries cancel out)

-- CASH ACCOUNT (should show decrease)
SELECT current_balance FROM cash_account WHERE id = 1;
-- Expected: 1000200.0 (decreased by 999800)

-- SUPPLIER BALANCES (should show they received credits from advance minus fees)
-- John: 75000 (stock) - 75000 (advance) - 100 (fee) = -100 (credit)
-- Mama: 60000 (stock) - 60000 (advance) - 100 (fee) = -100 (credit)

-- ADVANCE BALANCE (should show 865000 remaining)
SELECT amount_rwf - 135000 as remaining_advance
FROM unified_supplier_advance
WHERE transporter_name = 'Kasungu Ltd';
-- Expected: 865000.0 (1,000,000 - 75,000 - 60,000)
```

---

## 🧮 Ledger Reconciliation Summary

| Entity | Opening Balance | Changes | Closing Balance | Status |
|--------|-----------------|---------|-----------------|--------|
| **Transporter Kasungu Ltd** | 0 | +1,000,000 (advance) | **0** | ✅ Paid in full |
| | | -200 (fees) | | |
| | | -999,800 (payment) | | |
| **John Mwale (Supplier)** | 0 | +75,000 (stock) | **-100** | ✅ Credit balance |
| | | -75,000 (advance) | | |
| | | -100 (fee) | | |
| **Mama Charity (Supplier)** | 0 | +60,000 (stock) | **-100** | ✅ Credit balance |
| | | -60,000 (advance) | | |
| | | -100 (fee) | | |
| **Cash Account** | 2,000,000 | -999,800 (payment) | **1,000,200** | ✅ Decremented |
| **Advance Pool** | 1,000,000 | -135,000 (used) | **865,000** | ✅ Tracked |

---

## ✅ What We're Testing

✓ **Advance tracking** - Does unified_supplier_advance correctly store USD advance?  
✓ **Stock recording** - Are cassiterite_stock entries created properly?  
✓ **Advance allocation** - Does cassiterite_advance_allocation link advance to stocks?  
✓ **Business retention fees** - Are supplier_deduction entries created and transporter_ledger entries linked?  
✓ **Balance calculation** - Does calculate_consolidated_supplier_remaining_balance subtract fees?  
✓ **Transporter settlement** - Is payment amount correctly calculated (advance - fees)?  
✓ **Payment disbursement** - Are payment_review, cash_transaction, and transporter_ledger all linked?  
✓ **Cash account** - Is account balance properly debited?  
✓ **Ledger reconciliation** - Do all entries sum to zero showing no loss of money?

---

## 🚀 Ready for Live Testing

This scenario is ready to execute. We can:
1. **Create test data** in database directly with INSERT queries
2. **Run through UI** step-by-step in browser
3. **Verify each step** with the queries above
4. **Show complete linkages** between all tables
5. **Demonstrate ledger balancing** - everything adds up to zero

Which approach would you prefer?
