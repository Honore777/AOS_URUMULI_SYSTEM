from config import Config
from sqlalchemy import create_engine, text

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)

with engine.connect() as conn:
    q1 = "SELECT COALESCE(SUM(unit_percent),0) FROM copper_stock WHERE local_balance > 0"
    q2 = "SELECT COALESCE(SUM(local_balance),0) FROM copper_stock WHERE local_balance > 0"
    q3 = "SELECT COALESCE(SUM(t_unity),0) FROM copper_stock WHERE local_balance > 0"
    v1 = conn.execute(text(q1)).scalar() or 0
    v2 = conn.execute(text(q2)).scalar() or 0
    v3 = conn.execute(text(q3)).scalar() or 0

    if not v2:
        moyenne = 0
        moyenne_nb = 0
    else:
        moyenne = float(v1) / float(v2)
        moyenne_nb = float(v3) / float(v2)

    # Ensure aggregate row exists
    conn.execute(text("INSERT INTO stock_aggregate(mineral_type,total_quantity,total_weighted_percent,total_t_unity) SELECT 'copper',0,0,0 WHERE NOT EXISTS (SELECT 1 FROM stock_aggregate WHERE mineral_type='copper')"))
    conn.execute(text("COMMIT"))

    update_sql = f"UPDATE stock_aggregate SET total_quantity = {v2}, total_weighted_percent = {v1}, total_t_unity = {v3} WHERE mineral_type = 'copper'"

    print('Computed:', v1, v2, v3, 'moyenne=', moyenne, 'moyenne_nb=', moyenne_nb)

    r = conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + update_sql))
    rows = r.fetchall()
    print('\n--- EXPLAIN UPDATE stock_aggregate ---')
    for row in rows:
        print(row[0])
