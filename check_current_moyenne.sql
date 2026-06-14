-- Check current cassiterite moyenne calculation
SELECT 
    SUM(unit_percent) AS total_unit_percent,
    SUM(local_balance) AS total_remaining_balance,
    SUM(unit_percent) / SUM(local_balance) AS calculated_moyenne_decimal,
    (SUM(unit_percent) / SUM(local_balance)) * 100 AS calculated_moyenne_percent
FROM cassiterite_stock
WHERE local_balance > 0
AND is_deleted IS FALSE;

-- Check current copper/coltan moyenne calculation
SELECT 
    SUM(unit_percent) AS total_unit_percent,
    SUM(local_balance) AS total_remaining_balance,
    SUM(unit_percent) / SUM(local_balance) AS calculated_moyenne_decimal,
    (SUM(unit_percent) / SUM(local_balance)) * 100 AS calculated_moyenne_percent
FROM copper_stock
WHERE local_balance > 0
AND is_deleted IS FALSE;
