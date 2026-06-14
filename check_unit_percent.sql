-- Check unit_percent values for the specific stocks
SELECT 
    s.id,
    s.voucher_no,
    s.percentage,
    s.local_balance,
    s.unit_percent,
    s.local_balance * s.percentage / 100 AS expected_unit_percent,
    s.unit_percent - (s.local_balance * s.percentage / 100) AS difference
FROM cassiterite_stock s
WHERE s.voucher_no IN (
    'SSM/SN/2389',
    'SSM/SN/2417',
    'SSM/ TA/ 3843',
    'SSM/SN/2533',
    'SSM/SN/2544'
)
AND s.is_deleted IS FALSE;
