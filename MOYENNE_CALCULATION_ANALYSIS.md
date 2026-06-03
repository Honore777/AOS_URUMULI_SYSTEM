# Moyenne Calculation - Technical Deep Dive

## The Mystery: "Dashboard shows 69.7840% but optimization shows different value"

### Mathematical Proof of Equivalence

The dashboard and optimization SHOULD produce identical results when selecting ALL stocks.

**Dashboard Formula:**
```
moyenne = sum(all_stocks.unit_percent) / sum(all_stocks.local_balance)
        = sum(local_balance × percentage / 100) / sum(local_balance)
```

**Optimization Formula (when taking all quantities):**
```
moyenne = sum((unit_percent / local_balance) × qty) / sum(qty)

Where qty = local_balance for each selected stock:
       = sum(((local_balance × percentage / 100) / local_balance) × local_balance) / sum(local_balance)
       = sum(percentage / 100 × local_balance) / sum(local_balance)
       = sum(local_balance × percentage / 100) / sum(local_balance)
```

**Result: Both formulas are mathematically identical! ✓**

### Why Values Might Still Differ in Practice

#### 1. Different Stocks Selected
**Scenario:** User targets 69.78%, but optimization can't select ALL stocks and achieve exactly 69.78%. Instead, it selects a subset that gets closer.

**Example:**
```
All cassiterite stocks: 69.7840% (100 kg of various percentages)
  - Stock A: 68.5% (20 kg)
  - Stock B: 70.5% (30 kg)
  - Stock C: 69.8% (50 kg)

When optimization targets 69.78%:
- If it can select all three: achieves 69.7840% ✓ Matches dashboard
- If it can only select B+C (no A): achieves ~70.1% ≠ Dashboard
```

**How to verify:** Check if optimization selected all available stocks or just a subset.

#### 2. Precision/Rounding Issues
Floating point calculations can accumulate small rounding errors:
```
Dashboard:     69.78400000 (calculated once)
Optimization:  69.78399999 (calculated differently)
Difference:    0.00000001  (imperceptible but exists)
```

#### 3. Cache Staleness
The dashboard might show cached values from `StockAggregate` table:
```
UPDATE stock_aggregate
SET total_weighted_percent = sum(unit_percent),
    total_quantity = sum(local_balance)
WHERE id = 'cassiterite'
```

If stocks were recently added/deleted/modified, the cache might not reflect current state.

#### 4. Different Stocks in Filter Criteria
Dashboard might use date range or other filters that optimization doesn't:
```
Dashboard query: WHERE date BETWEEN '2026-01-01' AND '2026-05-31'
Optimization query: WHERE local_balance > 0
```

### Debug Steps to Identify the Issue

#### Step 1: Verify Dashboard Calculation
```python
from app import app, db
from cassiterite.models.stock import CassiteriteStock

with app.app_context():
    stocks = CassiteriteStock.query.filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False)
    ).all()
    
    sum_unit = sum(s.unit_percent or 0 for s in stocks)
    sum_qty = sum(s.local_balance or 0 for s in stocks)
    moyenne = sum_unit / sum_qty if sum_qty > 0 else 0
    
    print(f"Stocks selected: {len(stocks)}")
    print(f"Sum of unit_percent: {sum_unit}")
    print(f"Sum of local_balance: {sum_qty}")
    print(f"Calculated moyenne: {moyenne * 100:.4f}%")
```

#### Step 2: Check What Optimization Selected
In the result view, verify:
- Number of stocks selected
- Total quantity (kg) achieved
- Achieved moyenne percentage

#### Step 3: Check Unit Percent Values
```python
with app.app_context():
    stocks = CassiteriteStock.query.filter(
        CassiteriteStock.is_deleted.is_(False)
    ).all()
    
    for s in stocks:
        calculated_unit_percent = (s.local_balance * s.percentage / 100) if s.percentage else 0
        stored_unit_percent = s.unit_percent or 0
        
        if abs(calculated_unit_percent - stored_unit_percent) > 0.01:
            print(f"MISMATCH - Stock {s.id}:")
            print(f"  Stored unit_percent: {stored_unit_percent}")
            print(f"  Calculated unit_percent: {calculated_unit_percent}")
            print(f"  Difference: {calculated_unit_percent - stored_unit_percent}")
```

### Common Causes and Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| Optimization selects subset | Could be constraints or solver preference | Check number of stocks in result vs available |
| Values off by 0.0001% | Floating point rounding | This is normal and acceptable |
| Values differ by >0.1% | Different stocks selected or wrong percentages | Run debug steps above |
| Dashboard shows old value | Cache not updated | Call `CassiteriteStock.update_global_moyennes()` |
| Percentages stored wrong | Calculation error during stock creation | Check `calculate_unit_percentage()` in utils.py |

### If Dashboard and Optimization Still Don't Match

The optimization is CORRECT. It may be selecting a different subset of stocks to optimize for your target. This is expected behavior:

```
Dashboard moyenne = Average quality of ALL stocks
Optimization moyenne = Average quality of SELECTED stocks
                       (optimized to match your target)
```

They should only match if:
1. Optimization selected ALL available stocks
2. No intermediate rounding errors
3. Same date range / filters applied

### Verification Code

Use this to verify calculations are correct:

```python
from cassiterite_optimization import select_stocks_for_average_quality

# Get all stocks
with app.app_context():
    all_stocks = CassiteriteStock.query.filter(
        CassiteriteStock.local_balance > 0,
        CassiteriteStock.is_deleted.is_(False)
    ).all()
    
    # Calculate dashboard moyenne
    sum_unit = sum(s.unit_percent or 0 for s in all_stocks)
    sum_qty = sum(s.local_balance or 0 for s in all_stocks)
    dashboard_moyenne = (sum_unit / sum_qty * 100) if sum_qty > 0 else 0
    
    # Request optimization for that target
    selected, achieved_moyenne, achieved_qty = select_stocks_for_average_quality(
        target_moyenne=dashboard_moyenne,
        target_total_quantity=None
    )
    
    print(f"Dashboard moyenne: {dashboard_moyenne:.4f}%")
    print(f"Achieved moyenne: {achieved_moyenne*100:.4f}%")
    print(f"Match: {abs(dashboard_moyenne - (achieved_moyenne*100)) < 0.01}%")
    print(f"Selected {len(selected)} of {len(all_stocks)} stocks")
```

If selected = all stocks and moyennes match, calculations are correct. If not, investigate why optimizer selected subset.
