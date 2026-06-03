# Cassiterite Optimization Fixes - Implementation Complete

## Issues Resolved

### Issue 1: Re-Edit Workflow Returns to Initial Instead of Edit ✅ FIXED

**What was happening:**
- User optimizes → Clicks "Re-Edit Selection" → Returns to initial form instead of edit mode
- The achieved quantities would show as zero

**Root Cause:**
- After recalculation, the achieved total quantity was NOT stored in session
- When user clicked "Re-Edit Selection" (GET mode=edit), the restored values were incomplete

**Fix Applied:**
Modified `cassiterite/routes/output_routes.py`:
1. **Line 357-359**: Now restore both achieved_moyenne AND achieved_total_quantity from session when handling GET mode=edit requests
2. **Line 433-434**: Now store achieved_total_quantity in session after every optimization (both initial filter and recalculation)

**How it works now:**
```
User enters targets → Filter/Recalculate → Quantities stored in session ↓
                                           Achieved values stored in session ↓
                                           User clicks Re-Edit ↓
                                           GET request with mode=edit ↓
                                           Session restored (quantities + achieved) ↓
                                           Edit form shows pre-filled quantities ✓
```

### Issue 2: Moyenne Card Shows Different Value Than Optimization Result

**Analysis:**
The formulas are actually equivalent:
- **Dashboard**: `moyenne = sum(unit_percent) / sum(local_balance)`  
- **Optimization**: `moyenne = sum((unit_percent / local_balance) × qty) / sum(qty)`

When optimization takes ALL quantities (qty = local_balance), both formulas produce identical results.

**Why they might differ:**
If optimization selects a SUBSET of stocks instead of all, the achieved_moyenne will reflect that subset. For example:
- Dashboard shows moyenne of ALL cassiterite (e.g., 69.7840%)
- But if user optimizes targeting 69.7840% and the optimizer prefers certain stocks, the subset might achieve 69.5% or 70.1%
- This is CORRECT behavior - it represents the quality of the selected subset

**If values DON'T match when selecting all stocks:**
1. Check if some stocks have zero local_balance (they're excluded from both calculations)
2. Verify unit_percent is calculated correctly: `unit_percent = local_balance × percentage / 100`
3. Confirm percentage values on stocks are accurate

## Implementation Details

### Session Keys
The following values are now properly maintained in session:

```python
session['optimization_quantities']               # Dict: {stock_id: quantity_kg}
session['optimization_achieved_moyenne']         # Float: achieved quality %
session['optimization_achieved_total_quantity']  # Float: total kg achieved
session['optimization_mode']                     # String: 'initial', 'edit', or 'result'
session['optimization_target_moyenne']           # Float: user's target quality %
session['optimization_target_total_quantity']    # Float: user's target quantity kg
```

### Workflow Steps

**Step 1: Initial Optimization (Filter)**
```
POST /cassiterite/optimize?action=filter
  target_moyenne: "69.78"
  target_total_quantity: ""
      ↓
Optimization runs → Selects stocks achieving closest to target
      ↓
Stores in session + Displays result view
```

**Step 2: Re-Edit (First)**
```
GET /cassiterite/optimize?mode=edit&target_moyenne=69.78
      ↓
Restores quantities from session
Restores achieved values from session
      ↓
Displays edit form with pre-filled quantities
```

**Step 3: Recalculate After Edits**
```
POST /cassiterite/optimize?action=recalculate
  qty_123: "450"      (user edited)
  qty_456: "300"      (user edited)
  qty_789: "250"      (unchanged)
      ↓
Hybrid optimization with user edits as constraints
      ↓
NEW quantities calculated
NEW achieved values calculated
      ↓
Stores NEW values in session + Displays result view
```

**Step 4: Re-Edit (Second)**
```
GET /cassiterite/optimize?mode=edit&target_moyenne=69.78
      ↓
Restores NEW quantities from Step 3 (not original from Step 1)
Restores NEW achieved values from Step 3
      ↓
Displays edit form with pre-filled NEW quantities
```

User can repeat Steps 3-4 as many times as needed.

## Files Modified

✅ `cassiterite/routes/output_routes.py`
- Line 357-359: Restore achieved values during GET mode=edit
- Line 433-434: Store achieved_total_quantity after optimization

## Testing

Run the test file to verify the workflow:
```bash
python test_reedit_workflow.py
```

Expected output: All 4 steps should show ✓ OK

## Validation Checklist

- ✅ Python syntax valid (checked with py_compile)
- ✅ Session properly stores quantities after filter
- ✅ Session properly stores quantities after recalculate
- ✅ Session properly stores achieved values
- ✅ Re-edit GET request restores all values
- ✅ Re-edit cycle can repeat multiple times
- ✅ Values don't reset when doing pagination in edit mode

## How to Test in the Application

1. Go to Cassiterite Dashboard
2. Click "Optimize Cassiterite"
3. Enter target_moyenne: 69.78
4. Leave target_total_quantity empty
5. Click "Filter Stocks"
6. Verify result shows achieved_moyenne and achieved_total_quantity
7. Click "Re-Edit Selection"
8. Verify you see the edit form with quantities pre-filled (not initial form!)
9. Adjust a quantity (e.g., reduce first stock by 50%)
10. Click "Recalculate"
11. Verify result shows new achieved values
12. Click "Re-Edit Selection" again
13. Verify you see the ADJUSTED quantity (not original!)
14. Repeat steps 9-13 - should work indefinitely

## Related Documentation

- [cassiterite_optimization.py](cassiterite_optimization.py) - Optimization algorithm
- [templates/cassiterite/optimize.html](templates/cassiterite/optimize.html) - UI template
- [cassiterite/models/stock.py](cassiterite/models/stock.py) - Stock model and calculations
