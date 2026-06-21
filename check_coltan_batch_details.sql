-- Check the latest coltan batch details
SELECT 
    batch_id,
    created_at,
    plan_json->0 AS metadata
FROM bulk_output_plan
WHERE mineral_type = 'coltan'
AND status IN ('STOCK_CONFIRMED', 'EXECUTED')
ORDER BY created_at DESC
LIMIT 1;
