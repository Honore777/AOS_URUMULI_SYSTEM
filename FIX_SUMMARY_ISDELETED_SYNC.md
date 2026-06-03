# Fix Summary: is_deleted Filtering Sync & Achieved Moyenne Display

## Date: 2026-06-03
## Status: ✅ COMPLETE & VERIFIED

---

## Problem Statement

The application had **three critical issues**:

1. **Dashboard Moyenne Mismatch**: Dashboard "Cassiterite Stock" card was showing different Moyenne value (44.02%) than optimization engine (45.89%) because dashboard was including deleted stocks in calculation
2. **Achieved Moyenne Display Error**: Template displayed 0.46% instead of 45.89% because achieved_moyenne was calculated as decimal instead of percentage
3. **Inconsistent is_deleted Filtering**: Dashboard used no is_deleted filter while optimization engine used is_deleted.is_(False), causing data inconsistency

---

## Root Causes Identified

### Issue #1: Dashboard Missing is_deleted Filter
- **File**: `cassiterite/models/stock.py`
- **Method**: `update_global_moyennes()` (lines 230-232)
- **Problem**: Query filters by `local_balance > 0` but NOT by `is_deleted.is_(False)`
- **Impact**: Included deleted stocks in Moyenne calculation
- **Database Evidence**:
  - Total: 7 stocks
  - Active (is_deleted=False): 4 stocks = 55.60 kg @ 45.89% moyenne
  - Deleted (is_deleted=True): 3 stocks = 39.80 kg additional
  - Dashboard was calculating: All 7 stocks = 95.40 kg @ 44.02% moyenne
  - Optimization was calculating: 4 active stocks = 55.60 kg @ 45.89% moyenne

### Issue #2: Achieved Moyenne Display Formatting
- **Files**:
  - `cassiterite_optimization.py` (line 178)
  - `cassiterite/routes/output_routes.py` (lines 638-639)
- **Problem**: Functions returned achieved_moyenne as decimal (e.g., 0.4589) instead of percentage (45.89)
- **Template Issue**: `{{ "%.2f"|format(achieved_moyenne) }}%` displayed "0.46%" instead of "45.89%"
- **Root Cause**: Functions divided unit_percent by quantity but didn't multiply by 100

### Issue #3: Inconsistent is_deleted Filtering in Optimization Engine
- **File**: `cassiterite_optimization.py`
- **Lines**: 176-177 (in select_stocks_for_average_quality)
- **Problem**: total_unit_val and total_qty_val queries didn't explicitly include is_deleted filter
- **Impact**: Could theoretically calculate with deleted stocks

---

## Fixes Applied

### Fix #1: Add is_deleted Filter to Dashboard Calculation
**File**: `cassiterite/models/stock.py`
**Method**: `update_global_moyennes()` + `rebuild_stock_aggregate()`

**Before**:
```python
total_unit_percent = db.session.query(...).filter(CassiteriteStock.local_balance > 0).scalar()
total_remaining_balance = db.session.query(...).filter(CassiteriteStock.local_balance > 0).scalar()
total_t_unity = db.session.query(...).filter(CassiteriteStock.local_balance > 0).scalar()
```

**After**:
```python
total_unit_percent = db.session.query(...).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False)).scalar()
total_remaining_balance = db.session.query(...).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False)).scalar()
total_t_unity = db.session.query(...).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False)).scalar()
```

**Result**: Dashboard now correctly shows 45.8915% for 55.60 kg of active stocks only

### Fix #2: Multiply achieved_moyenne by 100 for Display
**Files**:
- `cassiterite_optimization.py` (line 178)
- `cassiterite/routes/output_routes.py` (lines 638-639)

**Before**:
```python
achieved_moyenne = (total_unit_val / total_qty_val) if total_qty_val > 0 else 0
achieved_moyenne_val = float(total_unit / total_qty) if total_qty else 0.0
```

**After**:
```python
achieved_moyenne = ((total_unit_val / total_qty_val) * 100) if total_qty_val > 0 else 0
achieved_moyenne_val = float((total_unit / total_qty) * 100) if total_qty else 0.0
```

**Result**: Template now displays 45.89% correctly instead of 0.46%

### Fix #3: Add Explicit is_deleted Filtering to Optimization Queries
**File**: `cassiterite_optimization.py`
**Lines**: 176-177, 378

**Before**:
```python
total_unit_val = db.session.query(...).filter(CassiteriteStock.id.in_(selected_ids)).scalar()
total_qty_val = db.session.query(...).filter(CassiteriteStock.id.in_(selected_ids)).scalar()
achieved_moyenne = (total_unit_val / total_qty_val) if total_qty_val > 0 else 0
```

**After**:
```python
total_unit_val = db.session.query(...).filter(CassiteriteStock.id.in_(selected_ids), CassiteriteStock.is_deleted.is_(False)).scalar()
total_qty_val = db.session.query(...).filter(CassiteriteStock.id.in_(selected_ids), CassiteriteStock.is_deleted.is_(False)).scalar()
achieved_moyenne = ((total_unit_val / total_qty_val) * 100) if total_qty_val > 0 else 0
```

Also fixed `select_stocks_with_minimum_quantities_cassiterite()` function (line 378) to multiply by 100.

---

## Verification Tests

### Test #1: Aggregate Rebuild with Correct Filter
```
Result: Dashboard aggregate rebuilt
  Total quantity: 55.60 kg (only active stocks)
  Total unit_percent: 25.515680
  Dashboard Moyenne: 45.8915%
  
Status: ✅ SYNCHRONIZED - Dashboard now shows correct 45.89%
```

### Test #2: Optimization Engine Returns Correct Percentage
```
Input: target_moyenne = 67.0%
Output: 
  Selected: 1 stock (Stock 11 at 35%)
  Achieved moyenne: 35.0000%
  Achieved quantity: 1.40 kg
  
Status: ✅ CORRECT - Returns percentage value, not decimal
```

### Test #3: Re-Edit Workflow Multiple Cycles
```
CYCLE 1 (Initial):
  Selected: 1 stock, Achieved: 35.00% moyenne, 1.40 kg
  Session stored: {11: 1.4}

CYCLE 2 (First Re-Edit):
  Restored from session: 35.00%, 1.40 kg
  After adjustment: 2.00 kg @ 35.00%
  Session updated: {11: 2.0}

CYCLE 3 (Second Re-Edit):
  Restored from session: 35.00%, 2.00 kg
  After adjustment: 0.80 kg @ 35.00%
  Session updated: {11: 0.8}

Status: ✅ RE-EDIT WORKFLOW WORKING CORRECTLY
- Session persistence working through multiple cycles
- Quantities properly restored and updated
- Achieved values maintained correctly
```

---

## Affected Components

| Component | Change | Status |
|-----------|--------|--------|
| Dashboard Moyenne Card | Added is_deleted filter | ✅ Fixed |
| Optimization Engine Output | Multiply by 100 for percentage | ✅ Fixed |
| Route Calculations | Multiply by 100 for percentage | ✅ Fixed |
| Optimization Queries | Add explicit is_deleted filter | ✅ Fixed |
| Re-Edit Workflow | No changes needed (was working) | ✅ Verified |
| Session Persistence | No changes needed (was working) | ✅ Verified |
| Template Display | No changes needed (now correct) | ✅ Verified |

---

## Data Consistency Now Achieved

### Before Fixes
- Dashboard: 44.02% (includes 3 deleted stocks)
- Optimization: 45.89% (excludes deleted stocks)
- **MISMATCH**: 1.87% difference

### After Fixes
- Dashboard: 45.8915% (only active stocks)
- Optimization: 45.89% (only active stocks)
- **SYNCHRONIZED**: ✅ Both use is_deleted.is_(False) filter

---

## Testing Checklist

- [x] Dashboard aggregate rebuilt with correct filter
- [x] Achieved moyenne displays as percentage (45.89% not 0.46%)
- [x] Optimization engine returns percentage values
- [x] Dashboard and optimization now synchronized
- [x] Re-edit workflow works through multiple cycles
- [x] Session properly persists quantities and achieved values
- [x] All queries explicitly filter by is_deleted.is_(False)

---

## Files Modified

1. `cassiterite/models/stock.py` - Lines 224-250 (update_global_moyennes and rebuild_stock_aggregate)
2. `cassiterite_optimization.py` - Lines 176-178, 378 (achieved_moyenne calculations)
3. `cassiterite/routes/output_routes.py` - Lines 638-639 (achieved_moyenne_val calculations)

---

## Deployment Notes

- Database aggregate should be rebuilt after deployment for full consistency
- Can be done via: `CassiteriteStock.update_global_moyennes()`
- No migration needed - fix is at application logic level
- Template no longer needs any changes for percentage display
