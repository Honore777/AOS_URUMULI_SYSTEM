-- Optimization engine formula:
-- total_unit = sum(unit_percent)
-- total_qty = sum(local_balance)
-- achieved_moyenne = total_unit / total_qty

-- At plan creation time (before outputs), local_balance = input_kg
-- So the formula becomes: sum(input_kg * percentage / 100) / sum(input_kg)

SELECT 
    SUM(s.input_kg * s.percentage / 100) AS total_unit,
    SUM(s.input_kg) AS total_qty,
    (SUM(s.input_kg * s.percentage / 100) / SUM(s.input_kg)) * 100 AS achieved_moyenne
FROM cassiterite_stock s
WHERE s.id IN (
    SELECT (item->>'stock_id')::int 
    FROM bulk_output_plan, jsonb_array_elements(plan_json::jsonb) AS item
    WHERE batch_id = 'batch_20260611_3ab304'
    AND item->>'stock_id' IS NOT NULL
);
