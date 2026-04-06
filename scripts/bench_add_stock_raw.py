import time
import sys
from datetime import datetime
sys.path.append(r'C:\Users\USER\final_smart_account_manager')
from config import Config
from sqlalchemy import create_engine, text

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)

def single_insert(i):
    voucher = f'BENCH_{int(time.time()*1000)}_{i}'
    date = datetime.utcnow().date().isoformat()
    input_kg = 100.0
    percentage = 12.0
    nb = 1.0
    u = nb * input_kg
    u_price = 100.0
    exchange = 1.0
    transport_tag = 0.0
    tot_amount_tag = transport_tag * input_kg
    rma = 150 * input_kg
    inkomane = 40 * input_kg
    amount = percentage * input_kg * exchange * u_price
    rra_3_percent = (50 * exchange * percentage * input_kg) * 3 / 100
    net_balance = (amount or 0) - (tot_amount_tag or 0) - (rma or 0) - (inkomane or 0) - (rra_3_percent or 0)
    local_balance = input_kg
    t_unity = nb * local_balance
    unit_percent = (local_balance * percentage) / 100.0 if percentage else 0

    insert_sql = text(
        "INSERT INTO copper_stock (date, voucher_no, supplier, input_kg, percentage, nb, u, u_price, exchange, transport_tag, tot_amount_tag, rma, inkomane, amount, rra_3_percent, net_balance, total_balance, local_balance, total_local_balance, unit_percent, t_unity)"
        " VALUES (:date, :voucher, :supplier, :input_kg, :percentage, :nb, :u, :u_price, :exchange, :transport_tag, :tot_amount_tag, :rma, :inkomane, :amount, :rra_3_percent, :net_balance, :total_balance, :local_balance, :total_local_balance, :unit_percent, :t_unity)"
    )

    # compute previous_total_balance
    with engine.connect() as conn:
        prev_total = conn.execute(text("SELECT COALESCE(SUM(net_balance),0) FROM copper_stock WHERE date <= :date"), {"date": date}).scalar() or 0
        total_balance = prev_total + net_balance

    start = time.perf_counter()
    with engine.begin() as conn:
        conn.execute(insert_sql, {
            'date': date,
            'voucher': voucher,
            'supplier': 'BENCH',
            'input_kg': input_kg,
            'percentage': percentage,
            'nb': nb,
            'u': u,
            'u_price': u_price,
            'exchange': exchange,
            'transport_tag': transport_tag,
            'tot_amount_tag': tot_amount_tag,
            'rma': rma,
            'inkomane': inkomane,
            'amount': amount,
            'rra_3_percent': rra_3_percent,
            'net_balance': net_balance,
            'total_balance': total_balance,
            'local_balance': local_balance,
            'total_local_balance': local_balance,
            'unit_percent': unit_percent,
            't_unity': t_unity,
        })

        # recompute aggregates and update stock_aggregate
        v1 = conn.execute(text("SELECT COALESCE(SUM(unit_percent),0) FROM copper_stock WHERE local_balance > 0")).scalar() or 0
        v2 = conn.execute(text("SELECT COALESCE(SUM(local_balance),0) FROM copper_stock WHERE local_balance > 0")).scalar() or 0
        v3 = conn.execute(text("SELECT COALESCE(SUM(t_unity),0) FROM copper_stock WHERE local_balance > 0")).scalar() or 0

        # ensure aggregate row
        conn.execute(text("INSERT INTO stock_aggregate(mineral_type,total_quantity,total_weighted_percent,total_t_unity) SELECT 'copper',0,0,0 WHERE NOT EXISTS (SELECT 1 FROM stock_aggregate WHERE mineral_type='copper')"))

        update_sql = text(f"UPDATE stock_aggregate SET total_quantity = :v2, total_weighted_percent = :v1, total_t_unity = :v3 WHERE mineral_type = 'copper'")
        conn.execute(update_sql, {'v1': v1, 'v2': v2, 'v3': v3})
    elapsed = (time.perf_counter() - start) * 1000.0
    return elapsed

if __name__ == '__main__':
    runs = 3
    results = []
    for i in range(runs):
        t = single_insert(i)
        print(f'Run {i+1}: {t:.1f} ms')
        results.append(t)
    print('Average:', sum(results)/len(results))
