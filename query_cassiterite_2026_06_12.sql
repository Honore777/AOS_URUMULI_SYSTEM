-- Cassiterite stocks for 2026-06-12 (Friday) with supplier
-- Using aliasing to match table format (excluding NB, U, MOYENNE NB which don't exist in cassiterite)
-- Adding cassiterite-specific columns: LME, M_LME, SEC, TC, AMOUNT WITH TAXES, BALANCE TO PAY
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
    u AS "U",
    u_price AS "U. PRICE",
    moyenne AS "MOYENNE PRICE",
    exchange AS "EXCHANGE",
    amount AS "AMOUNT",
    amount_with_taxes AS "AMOUNT WITH TAXES",
    transport_tag AS "TRANSP.(TAG)",
    tot_amount_tag AS "TOT.AMONT TAG",
    rma AS "RMA",
    inkomane AS "INKOMANE",
    rra_3_percent AS "TAXES(3%)",
    lme AS "LME",
    m_lme AS "M_LME",
    sec AS "SEC",
    tc AS "TC",
    balance_to_pay AS "BALANCE TO PAY"
FROM cassiterite_stock
WHERE date = '2026-06-12'
AND is_deleted IS FALSE
ORDER BY date DESC, voucher_no;
