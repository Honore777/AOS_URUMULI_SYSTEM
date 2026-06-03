# StockAggregate Out-of-Sync Analysis

## How StockAggregate Can Get Out of Sync

### 1. Initial Data Migration
- Stocks added before StockAggregate system was implemented won't be in aggregate
- Your case: 2kg in aggregate vs 55.60kg actual suggests this

### 2. Direct Database Modifications
- Manual SQL updates bypass application logic
- Example: `UPDATE cassiterite_stock SET local_balance = 50 WHERE id = 10`

### 3. Silent Failures in Delta Updates
- All `apply_aggregate_delta` calls are wrapped in try/except that logs but doesn't fail
- If exception occurs, aggregate update is silently skipped

### 4. Transaction Rollbacks After Aggregate Update
- If transaction updates aggregate then rolls back, aggregate stays modified while stock changes revert

### 5. Missing Code Paths
- Bulk imports, data correction scripts, or manual adjustments might not call `apply_aggregate_delta`

## Current Code Paths That Update Aggregate

### Cassiterite:
- add_stock (stock_routes.py:318-323)
- delete_stock (stock_routes.py:450-454)  
- edit_stock (stock_routes.py:696-704)
- record_output (output_routes.py:106-112)

## Solution
Admin page now has "Rebuild Cassiterite Aggregate" and "Rebuild Copper Aggregate" buttons to resync from current stock data.
