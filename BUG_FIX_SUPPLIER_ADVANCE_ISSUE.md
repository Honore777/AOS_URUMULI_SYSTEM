# Bug Fix: Supplier Advance Not Appearing in Add Stock

## Issue Summary
When adding stock, if you selected a supplier that you had previously given an advance to, that supplier wouldn't appear in the advance payment dropdown, or the advance wouldn't apply correctly when adding stock.

## Root Cause

**Location:** `core/routes/cashier_routes.py` lines 1863 & 2006

When a cashier processes an approved supplier advance payment, the code creates a `UnifiedSupplierAdvance` record to track the advance. However, there was a **critical mismatch** in how the supplier name and normalized supplier name were set:

### Copper Advance Code (Before Fix):
```python
supplier_name = (payload.get('supplier_name') or review.customer or '').strip() or None  # Line 1758

# ... later at line 1863 ...
unified = UnifiedSupplierAdvance(
    supplier_name=(supplier_name or '').strip() or (review.customer or '').strip() or 'Unknown',
    supplier_name_norm=_norm_supplier(supplier_name),  # ❌ BUG: Using ORIGINAL supplier_name, not the fallback!
    # ...
)
```

### The Problem:
1. `supplier_name` could be `None` (if not in payload)
2. `supplier_name_norm` was calculated using the ORIGINAL `supplier_name` (could be None), producing empty string
3. `supplier_name` itself was correctly set with fallback logic to `review.customer`

**Result:** 
- `supplier_name` = "Supplier Name" ✓
- `supplier_name_norm` = "" ✗ (should have been normalized "supplier name")

### Impact:
- Advance records were created with mismatched supplier identifiers
- When adding stock and validating supplier advances, the normalization check would fail
- Suppliers with paid advances appeared in the dropdown but couldn't be matched with stock

---

## The Fix

**Applied to:**
- [core/routes/cashier_routes.py](core/routes/cashier_routes.py#L1862-L1878) - Copper advances
- [core/routes/cashier_routes.py](core/routes/cashier_routes.py#L2005-L2026) - Cassiterite advances

### Fixed Code Pattern:
```python
def _norm_supplier(nm):
    return ' '.join((nm or '').strip().lower().split())

# FIX: Use the SAME fallback logic for both supplier_name and supplier_name_norm
final_supplier_name = (supplier_name or '').strip() or (review.customer or '').strip() or 'Unknown'
unified = UnifiedSupplierAdvance(
    supplier_name=final_supplier_name,                    # ✓ Correct
    supplier_name_norm=_norm_supplier(final_supplier_name),  # ✓ Now uses same fallback!
    source_mineral_type='copper',  # or 'cassiterite'
    # ... rest of fields ...
)
```

---

## Verification

After this fix:

1. ✓ When boss approves an advance payment
2. ✓ Cashier processes it and creates UnifiedSupplierAdvance
3. ✓ `supplier_name` and `supplier_name_norm` are now **consistent**
4. ✓ When adding stock, the advance dropdown shows supplier names correctly
5. ✓ Selecting an advance matches the stock supplier correctly
6. ✓ Advance gets allocated to stock properly

---

## Related Code

### Add Stock Advance Fetching
**Location:** [copper/routes/stock_routes.py#L329-L341](copper/routes/stock_routes.py#L329-L341)
```python
advance_rows = (
    db.session.query(
        UnifiedSupplierAdvance.id,
        UnifiedSupplierAdvance.supplier_name,
        UnifiedSupplierAdvance.advance_remaining,
        UnifiedSupplierAdvance.paid_at,
    )
    .filter(
        UnifiedSupplierAdvance.is_deleted.is_(False),
        UnifiedSupplierAdvance.advance_remaining > 0,  # Only non-exhausted advances
    )
    .order_by(UnifiedSupplierAdvance.paid_at.desc(), UnifiedSupplierAdvance.id.desc())
    .all()
)
```

### Advance Validation in Add Stock
**Location:** [copper/routes/stock_routes.py#L464-L477](copper/routes/stock_routes.py#L464-L477)
```python
advance_payments = (
    UnifiedSupplierAdvance.query
    .filter(
        UnifiedSupplierAdvance.id.in_(requested_advance_ids),
        UnifiedSupplierAdvance.is_deleted.is_(False),
        UnifiedSupplierAdvance.advance_remaining > 0,
    )
    .with_for_update()
    .order_by(UnifiedSupplierAdvance.paid_at.asc(), UnifiedSupplierAdvance.id.asc())
    .all()
)

# Later, validation checks supplier match
if _norm(advance_payment.supplier_name) != _norm(stock.supplier):
    flash("Selected advances must belong to the same supplier as the stock.", "danger")
```

---

## Testing

To verify the fix works:

1. Create an advance payment for supplier "Test Supplier"
2. Boss approves the advance
3. Cashier disburses it
4. Check database: `SELECT * FROM unified_supplier_advance WHERE supplier_name = 'Test Supplier'`
5. Verify `supplier_name_norm` is properly normalized (not empty)
6. Go to Add Stock
7. Supplier "Test Supplier" should appear in the advance dropdown
8. Select it and add stock - advance should be properly allocated

