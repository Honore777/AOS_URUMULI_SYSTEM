# Payment History Guard Debugging - Summary of Changes

## Problem
User reports they can edit/delete stocks with supplier payments, even though a guard function should block this.

## Solution Implemented
Added comprehensive debugging to trace why the guard isn't working.

### 1. Enhanced Logging in Stock Routes

**File: `copper/routes/stock_routes.py`**
- Added detailed logging to `_stock_has_payment_history()` function (lines ~28-35):
  - Logs the query result
  - Logs the boolean outcome
  - Catches exceptions with error logging
  
- Added logging to `delete_stock()` at line ~99:
  - Logs: `delete_stock: stock_id=X has_payments=Y (bool=Z)`
  
- Added logging to `edit_stock()` at line ~267:
  - Logs: `edit_stock: stock_id=X has_payments=Y (bool=Z)`

**File: `cassiterite/routes/stock_routes.py`**
- Same logging added to cassiterite equivalents:
  - `_stock_has_payment_history()` function (lines ~26-33)
  - `delete_stock()` route (lines ~265-266)
  - `edit_stock()` route (lines ~405-406)

### 2. Debug API Endpoint

**Added to `copper/routes/stock_routes.py` (line ~98)**
```
GET /api/debug/stock/<stock_id>/check_payments
```

Returns JSON with:
- `stock_id`: ID being checked
- `voucher_no`: Stock's voucher number
- `total_payments`: Count of payment records found
- `payments`: Array of payment details (ID, amount, is_deleted, payment_type)
- `guard_result`: Boolean - whether guard should block edits (true if payments exist)

### 3. Debug Script

**Created: `debug_payment_guard.py`**

Standalone Python script to check a stock's payment history:

```bash
python debug_payment_guard.py copper 789
python debug_payment_guard.py cassiterite 45
```

Output includes:
- Stock details (voucher, supplier, quantities)
- All payments found for that stock
- Clear indication of whether guard should block edits

## Testing Steps

1. **Restart Flask app** (critical - code changes need fresh reload)

2. **Run debug script:**
   ```bash
   python debug_payment_guard.py copper STOCK_ID
   ```

3. **Check the logs** for messages like:
   - `edit_stock: stock_id=789 has_payments=True`
   - `_stock_has_payment_history: stock_id=789 result=<SupplierPayment id=42>`

4. **Test via API:**
   ```
   http://localhost:5000/api/debug/stock/789/check_payments
   ```

5. **Test via UI:**
   - Go to dashboard
   - Click Edit on a stock with payments
   - If guard works: See flash message + redirect to dashboard
   - If guard fails: Edit form appears

## Expected Outcomes

### Scenario A: Guard is working ✓
- Debug script shows payments exist
- API endpoint returns `"total_payments": N` (N > 0)
- Log shows `has_payments=True`
- UI edit attempt creates PaymentReview (requires boss approval)

### Scenario B: Guard not working ❌
- Debug script shows payments exist
- API endpoint returns `"total_payments": N` (N > 0)
- But UI still allows free edit
- **Likely cause:** Flask app not restarted (still running old code)

### Scenario C: Stock has no payments
- Debug script shows `total_payments: 0`
- API endpoint returns `"guard_result": false`
- UI allows free edit (correct behavior)
- **Note:** If this stock SHOULD have payments, investigate why they're missing

## Possible Issues to Investigate

1. **Flask app not restarted**
   - Solution: Stop and restart the Flask development server

2. **Payments exist but in different table**
   - Check: Are they in `UnifiedSupplierAdvance` instead of `SupplierPayment`?
   - Check: Are they soft-deleted (is_deleted=True)?

3. **Foreign key misconfiguration**
   - Check: Are stock_id values NULL in SupplierPayment table?
   - Run: `SELECT * FROM supplier_payment WHERE stock_id = 789;`

4. **Session/transaction issue**
   - Payments created but not committed yet
   - Solution: Ensure all payment creates do `db.session.commit()`

## Code Guard Logic (now in both copper + cassiterite)

```python
def _stock_has_payment_history(stock_id: int) -> bool:
    """Return True if this stock has ever had supplier payments recorded."""
    try:
        result = db.session.query(SupplierPayment.id)\
            .filter(SupplierPayment.stock_id == stock_id).first()
        has_payment = result is not None
        logger.debug("_stock_has_payment_history: stock_id=%s result=%s has_payment=%s", 
                     stock_id, result, has_payment)
        return has_payment
    except Exception as e:
        logger.exception("_stock_has_payment_history failed for stock_id=%s", stock_id)
        return False

# In delete_stock():
has_payments = _stock_has_payment_history(stock_id)
if has_payments:
    # Create PaymentReview (requires boss approval)
    ...
    return redirect(...)

# In edit_stock():
has_payments = _stock_has_payment_history(stock_id)
if has_payments:
    # Create PaymentReview (requires boss approval)
    ...
    return redirect(...)
```

## Next Steps

1. **Provide results from debug steps above**
2. If payments aren't found:
   - Check database directly: `SELECT * FROM supplier_payment WHERE stock_id = X;`
   - Verify stock creation date vs payment dates
3. If payments are found but guard still doesn't block:
   - Check Flask logs for full exception traces
   - Verify app was actually restarted
4. If everything works:
   - Guard is functional ✓
   - Can proceed with additional testing
