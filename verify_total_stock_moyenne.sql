-- Calculate moyenne and nobelium for all remaining copper/coltan stocks
SELECT 
    SUM((percentage / 100.0) * local_balance) AS total_weighted_percentage,
    SUM(nb * local_balance) AS total_weighted_nobelium,
    SUM(local_balance) AS total_quantity,
    (SUM((percentage / 100.0) * local_balance) / SUM(local_balance)) * 100 AS achieved_moyenne_percent,
    SUM(nb * local_balance) / SUM(local_balance) AS achieved_nobelium
FROM copper_stock
WHERE local_balance > 0
AND is_deleted IS FALSE;
