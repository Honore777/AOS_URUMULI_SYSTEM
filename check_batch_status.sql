-- Check the status of the latest coltan batch
SELECT 
    batch_id,
    created_at,
    status,
    mineral_type,
    plan_json->0 AS metadata
FROM bulk_output_plan
WHERE mineral_type = 'coltan'
ORDER BY created_at DESC
LIMIT 5;
