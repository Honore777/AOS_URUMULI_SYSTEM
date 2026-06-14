-- Check nobelium (nb) values for copper/coltan
SELECT 
    s.id,
    s.voucher_no,
    s.nb,
    s.local_balance,
    s.t_unity,
    s.nb * s.local_balance AS expected_t_unity,
    s.t_unity - (s.nb * s.local_balance) AS difference
FROM copper_stock s
WHERE s.local_balance > 0
AND s.is_deleted IS FALSE
ORDER BY s.id
LIMIT 10;
