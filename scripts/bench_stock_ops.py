import sys
import time
from datetime import datetime

sys.path.append(r'C:\Users\USER\final_smart_account_manager')

from app import app, db


def now_ms():
    return int(time.time() * 1000)


with app.app_context():
    from copper.models import CopperStock
    from cassiterite.models import CassiteriteStock
    from core.models import StockAggregate

    def read_agg(mineral):
        agg = db.session.query(StockAggregate).filter_by(mineral_type=mineral).first()
        if not agg:
            return None
        return (float(agg.total_quantity or 0.0), float(agg.total_weighted_percent or 0.0), float(agg.total_t_unity or 0.0))

    def print_agg(mineral, label=''):
        v = read_agg(mineral)
        print(f"{label} {mineral} aggregate:", v)

    print('=== Initial aggregates ===')
    print_agg('copper', 'Before:')
    print_agg('cassiterite', 'Before:')

    # Copper: add -> edit -> delete
    print('\n=== Copper bench ===')
    voucher = f'BENCH_COP_{now_ms()}'
    s = CopperStock(
        date=datetime.utcnow().date(),
        voucher_no=voucher,
        supplier='BENCH',
        input_kg=100.0,
        percentage=12.0,
        nb=1.0,
        u=1.0 * 100.0,
        u_price=100.0,
        exchange=1.0,
        transport_tag=0.0,
        tot_amount_tag=0.0,
        rma=0.0,
        inkomane=0.0,
        amount=0.0,
        rra_3_percent=0.0,
        net_balance=0.0,
        total_balance=0.0,
    )

    t0 = time.perf_counter()
    try:
        db.session.add(s)
        db.session.flush()
        s.update_calculations()
        # apply delta to aggregate (simulate route behavior)
        try:
            q, wp, t = CopperStock.contribution(s)
            CopperStock.apply_aggregate_delta(q, wp, t)
        except Exception as e:
            print('Copper add: apply_aggregate_delta failed', e)

        db.session.commit()
    except Exception as e:
        print('Copper add: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Copper add elapsed: {(t1-t0)*1000:.1f} ms, id={getattr(s, "id", None)}')
    print_agg('copper', 'After add:')

    # Edit
    t0 = time.perf_counter()
    try:
        # compute old contribution
        old_q, old_wp, old_t = CopperStock.contribution(s)

        s.input_kg = 50.0
        s.percentage = 10.0
        s.update_calculations()

        # apply delta
        try:
            new_q, new_wp, new_t = CopperStock.contribution(s)
            CopperStock.apply_aggregate_delta(new_q - old_q, new_wp - old_wp, new_t - old_t)
        except Exception as e:
            print('Copper edit: apply_aggregate_delta failed', e)

        db.session.commit()
    except Exception as e:
        print('Copper edit: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Copper edit elapsed: {(t1-t0)*1000:.1f} ms')
    print_agg('copper', 'After edit:')

    # Delete
    t0 = time.perf_counter()
    try:
        # remove contribution and delete
        try:
            q, wp, t = CopperStock.contribution(s)
        except Exception:
            q = wp = t = 0.0

        db.session.delete(s)
        db.session.flush()
        try:
            CopperStock.apply_aggregate_delta(-q, -wp, -t)
        except Exception as e:
            print('Copper delete: apply_aggregate_delta failed', e)
        db.session.commit()
    except Exception as e:
        print('Copper delete: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Copper delete elapsed: {(t1-t0)*1000:.1f} ms')
    print_agg('copper', 'After delete:')

    # Cassiterite: add -> edit -> delete
    print('\n=== Cassiterite bench ===')
    voucher = f'BENCH_CASS_{now_ms()}'
    s2 = CassiteriteStock(
        date=datetime.utcnow().date(),
        voucher_no=voucher,
        supplier='BENCH',
        input_kg=100.0,
        percentage=11.0,
        lme=200.0,
        m_lme=0.0,
        sec=0.0,
        tc=0.0,
        exchange=1.0,
        transport_tag=0.0,
        rma=0.0,
        inkomane=0.0,
    )

    t0 = time.perf_counter()
    try:
        db.session.add(s2)
        db.session.flush()
        s2.update_calculations()
        # apply delta to aggregate (simulate route behavior)
        try:
            q2, wp2, t2 = CassiteriteStock.contribution(s2)
            CassiteriteStock.apply_aggregate_delta(q2, wp2, t2)
        except Exception as e:
            print('Cass add: apply_aggregate_delta failed', e)

        db.session.commit()
    except Exception as e:
        print('Cass add: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Cassiterite add elapsed: {(t1-t0)*1000:.1f} ms, id={getattr(s2, "id", None)}')
    print_agg('cassiterite', 'After add:')

    # Edit
    t0 = time.perf_counter()
    try:
        # compute old contribution
        try:
            old_q2, old_wp2, old_t2 = CassiteriteStock.contribution(s2)
        except Exception:
            old_q2 = old_wp2 = old_t2 = 0.0

        s2.input_kg = 40.0
        s2.percentage = 9.0
        s2.update_calculations()

        # apply delta
        try:
            new_q2, new_wp2, new_t2 = CassiteriteStock.contribution(s2)
            CassiteriteStock.apply_aggregate_delta(new_q2 - old_q2, new_wp2 - old_wp2, new_t2 - old_t2)
        except Exception as e:
            print('Cass edit: apply_aggregate_delta failed', e)

        db.session.commit()
    except Exception as e:
        print('Cass edit: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Cassiterite edit elapsed: {(t1-t0)*1000:.1f} ms')
    print_agg('cassiterite', 'After edit:')

    # Delete
    t0 = time.perf_counter()
    try:
        try:
            q2, wp2, t2 = CassiteriteStock.contribution(s2)
        except Exception:
            q2 = wp2 = t2 = 0.0

        db.session.delete(s2)
        db.session.flush()
        # apply negative delta to aggregate to remove this stock's contribution
        try:
            CassiteriteStock.apply_aggregate_delta(-q2, -wp2, -t2)
        except Exception as e:
            print('Cass delete: apply_aggregate_delta failed', e)

        db.session.commit()
    except Exception as e:
        print('Cass delete: exception', e)
        try:
            db.session.rollback()
        except Exception:
            pass
    t1 = time.perf_counter()
    print(f'Cassiterite delete elapsed: {(t1-t0)*1000:.1f} ms')
    print_agg('cassiterite', 'After delete:')

    print('\n=== Bench complete ===')
