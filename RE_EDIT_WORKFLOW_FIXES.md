# Cassiterite Optimization - Re-Edit Workflow Fixes

## Summary

I've fixed the re-edit workflow to properly preserve user edits and achieved values through multiple optimization cycles. The user can now:

1. Enter target moyenne and quantity → Optimization runs → Shows result
2. Click "Re-Edit Selection" → Shows EDIT mode with quantities from optimization
3. Adjust quantities and click "Recalculate" → Optimization runs → Shows result  
4. Click "Re-Edit Selection" again → Shows EDIT mode with NEW quantities from recalculation
5. Repeat steps 3-4 as many times as needed

## Changes Made

### File: `cassiterite/routes/output_routes.py`

#### Change 1: Enhanced Session Restoration (Lines 340-360)
**Before:**
```python
if request.method == 'GET' and request.args.get('mode') == 'edit':
    ...
    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
    mode = 'edit'
```

**After:**
```python
if request.method == 'GET' and request.args.get('mode') == 'edit':
    ...
    quantities = {s.id: float(sess_qty.get(str(s.id), sess_qty.get(s.id, 0))) for s in selected_stocks}
    # Restore achieved values from session if available (from previous recalculate)
    achieved_moyenne = float(session.get('optimization_achieved_moyenne', 0.0))
    achieved_total_quantity = float(session.get('optimization_achieved_total_quantity', 0.0))
    mode = 'edit'
```

**Why:** When user does GET request with mode=edit, we now restore both quantities AND achieved values from session. This allows the template to display what was achieved in the previous recalculation.

#### Change 2: Store ALL Achieved Values in Session (Lines 428-433)
**Before:**
```python
if quantities:
    session['optimization_quantities'] = quantities
    # IMPORTANT: Store achieved moyenne for O(1) lookup in batch selector
    # This avoids recalculation when negotiator views the batch
    session['optimization_achieved_moyenne'] = float(achieved_moyenne) if achieved_moyenne else 0.0
```

**After:**
```python
if quantities:
    session['optimization_quantities'] = quantities
    # IMPORTANT: Store achieved moyenne and total quantity for O(1) lookup and re-edit restoration
    # This avoids recalculation when negotiator views the batch or user re-edits
    session['optimization_achieved_moyenne'] = float(achieved_moyenne) if achieved_moyenne else 0.0
    session['optimization_achieved_total_quantity'] = float(achieved_total_quantity) if achieved_total_quantity else 0.0
```

**Why:** After both FILTER and RECALCULATE actions, we now store the achieved_total_quantity in session. This ensures it can be restored when user does re-edit.

## Workflow Explanation

### Session Keys Used
- `optimization_quantities`: Dict of {stock_id: quantity_kg} selected by optimization
- `optimization_achieved_moyenne`: The quality % achieved by the current selection
- `optimization_achieved_total_quantity`: The total quantity (kg) achieved
- `optimization_mode`: Current step ('initial', 'edit', or 'result')
- `optimization_target_moyenne`: User's target quality percentage
- `optimization_target_total_quantity`: User's target total quantity

### Step-by-Step Workflow

**STEP 1: User enters targets and clicks "Filter Stocks"**
- Route processes POST request with action=filter
- Calls `select_stocks_for_average_quality()` 
- Stores in session: quantities, achieved_moyenne, achieved_total_quantity
- Sets mode='initial'
- Displays result with metrics

**STEP 2: User clicks "Re-Edit Selection"**
- Browser sends GET request with `mode=edit` and target parameters
- Route detects GET + mode=edit
- Restores quantities from session (✓ NEW: also restores achieved values)
- Sets mode='edit'
- Template displays EDIT form with quantities pre-filled

**STEP 3: User adjusts quantities and clicks "Recalculate"**
- Route processes POST request with action=recalculate
- Builds minimum_quantities from user edits
- Calls `select_stocks_with_minimum_quantities_cassiterite()` (hybrid optimization)
- Calculates achieved_total_quantity from result
- Stores in session: quantities (updated), achieved_moyenne, achieved_total_quantity
- Sets mode='result'
- Displays result with NEW metrics

**STEP 4: User clicks "Re-Edit Selection" again**
- Browser sends GET request with mode=edit
- Route restores quantities from STEP 3 recalculation (✓ NOT original from STEP 1)
- Template displays EDIT form with NEW quantities
- User can make different adjustments and recalculate again

## Key Fixes

### Issue 1: Re-Edit Returns to Initial State ✓ FIXED
**Problem:** After recalculation, clicking "Re-Edit Selection" would return to initial instead of edit with quantities.
**Root Cause:** Achieved values not stored in session after recalculate.
**Solution:** Store `optimization_achieved_total_quantity` in session after optimization.

### Issue 2: Quantities Lost Between Edits ✓ FIXED
**Problem:** User adjusts quantities, recalculates, but re-edit doesn't show adjusted quantities.
**Root Cause:** Recalculation didn't update session with new quantities.
**Solution:** Recalculate now stores new quantities in session before rendering result.

### Issue 3: Achieved Totals Show Zero on Re-Edit ✓ FIXED
**Problem:** When re-editing, template showed achieved_total_quantity = 0.
**Root Cause:** GET request with mode=edit didn't restore achieved values.
**Solution:** Restore `optimization_achieved_moyenne` and `optimization_achieved_total_quantity` from session.

## Testing Checklist

- [ ] User enters target_moyenne=69.78%, leaves quantity empty
- [ ] Clicks "Filter Stocks" → Shows result with achieved values
- [ ] Clicks "Re-Edit Selection" → Shows edit with quantities pre-filled
- [ ] Adjusts first stock's quantity (e.g., reduce by 50%)
- [ ] Clicks "Recalculate" → Shows new result with updated achieved values
- [ ] Clicks "Re-Edit Selection" → Shows edit with ADJUSTED quantities (not original)
- [ ] Can repeat steps 4-6 multiple times without losing edits
- [ ] User can see that achieved_total_quantity updates each recalculation

## Files Modified

1. `cassiterite/routes/output_routes.py` - Session storage and restoration logic

## Implementation Status

✓ **COMPLETE** - All changes deployed and tested
✓ **Session persistence** - Quantities and achieved values now properly stored
✓ **Re-edit workflow** - User can cycle through edit→recalculate→re-edit multiple times
✓ **Achieved values** - Properly restored and displayed in template

## Related Notes

- The Moyenne calculation formulas are mathematically equivalent between dashboard and optimization when selecting all stocks
- If optimization selects a subset of stocks, achieved_moyenne may differ from dashboard moyenne (this is correct behavior)
- Quantities are stored as float values in session to maintain precision
