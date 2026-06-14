-- Verify a few copper/coltan stocks to ensure the fix is correct
SELECT 
    s.id,
    s.voucher_no,
    s.percentage,
    s.local_balance,
    s.unit_percent,
    s.local_balance * s.percentage / 100 AS expected_unit_percent,
    s.unit_percent - (s.local_balance * s.percentage / 100) AS difference
FROM copper_stock s
WHERE s.local_balance > 0
AND s.is_deleted IS FALSE
ORDER BY s.id
LIMIT 10;
