-- Coltan/Copper stocks for 2026-06-12 (Friday) with supplier
-- Using aliasing to match table format
SELECT 
    date AS "DATES",
    voucher_no AS "VOUCHER NO",
    supplier AS "SUPPLIER",
    input_kg AS "IN PUT",
    local_balance AS "BALANCE",
    percentage AS "PERCENTAGE",
    unit_percent AS "UNIT %",
    moyenne AS "MOYENNE",
    local_balance AS "QTY",
    nb AS "NB",
    u AS "U",
    moyenne_nb AS "MOYENNE NB",
    u_price AS "U. PRICE",
    moyenne AS "MOYENNE PRICE",
    exchange AS "EXCHANGE",
    amount AS "AMOUNT",
    transport_tag AS "TRANSP.(TAG)",
    tot_amount_tag AS "TOT.AMONT TAG",
    rma AS "RMA",
    inkomane AS "INKOMANE",
    rra_3_percent AS "TAXES(3%)"
FROM copper_stock
WHERE date = '2026-06-12'
AND is_deleted IS FALSE
ORDER BY date DESC, voucher_no;
