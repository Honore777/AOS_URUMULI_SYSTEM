-- Calculate moyenne for all remaining cassiterite stocks
SELECT 
    SUM((percentage / 100.0) * local_balance) AS total_weighted_percentage,
    SUM(local_balance) AS total_quantity,
    (SUM((percentage / 100.0) * local_balance) / SUM(local_balance)) * 100 AS achieved_moyenne_percent
FROM cassiterite_stock
WHERE local_balance > 0
AND is_deleted IS FALSE;
