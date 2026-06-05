"""
Cassiterite Optimization Functions
Follows the same hybrid binary/continuous pattern as copper
"""
from pulp import LpProblem, LpVariable, lpSum, LpMinimize, LpBinary, LpContinuous, PULP_CBC_CMD
from cassiterite.models import CassiteriteStock
from types import SimpleNamespace
from config import db
import os
import time
import logging
from utils import trace_time
from sqlalchemy import func

logger = logging.getLogger(__name__)


@trace_time
def select_stocks_for_average_quality(target_moyenne=None, target_total_quantity=None, minimize_quantity=False):
    """
    Binary selection: Select stocks to achieve target average quality.
    Each stock is either selected (1) or not (0) - takes ALL available if selected.
    
    Used in STEP 1 of optimization.
    
    Args:
        target_moyenne: Target average purity/quality %
        target_total_quantity: Optional target total quantity (kg) to achieve
        minimize_quantity: If True, prefer smaller totals as tie-breaker
    
    Returns:
        (selected_stocks_list, achieved_moyenne, achieved_total_quantity)
    """
    rows = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.unit_percent,
        CassiteriteStock.local_balance,
    ).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False)).all()

    if not rows:
        return [], 0, 0.0

    remaining_stocks = [SimpleNamespace(id=r[0], unit_percent=float(r[1] or 0), local_balance=float(r[2] or 0)) for r in rows]
    stock_vars = {s.id: LpVariable(f"stock{s.id}", cat=LpBinary) for s in remaining_stocks}
    
    prob = LpProblem("Cassiterite_Stock_Selection_Binary", LpMinimize)
    
    # Total unit contribution (quality * quantity)
    total_unit = lpSum(
        s.unit_percent * stock_vars[s.id] 
        for s in remaining_stocks
    )
    
    # Total quantity
    total_qty = lpSum(
        s.local_balance * stock_vars[s.id] 
        for s in remaining_stocks
    )
    
    # Objective: minimize error from target moyenne
    # target_moyenne is entered as percentage (e.g., 69.0 for 69%)
    # but total_unit is in decimal units, so scale target by /100
    objective_terms = []
    if target_moyenne is not None:
        target_moyenne_scaled = target_moyenne / 100.0
        error = LpVariable("error_moyenne", lowBound=0)
        prob += error >= total_unit - (target_moyenne_scaled * total_qty)
        prob += error >= -(total_unit - (target_moyenne_scaled * total_qty))
        objective_terms.append(error)
    else:
        # If no target, prefer smaller total_qty (minimize quantity) or prefer higher quality?
        # Default behavior: minimize total_qty to keep selection compact.
        objective_terms.append(total_qty)

    # Minimize quantity error when a target_total_quantity is provided.
    # Also enforce a clamped lower bound on total_qty to avoid the solver
    # trivially minimizing the absolute quality error by selecting tiny totals.
    if target_total_quantity is not None:
        try:
            tgt_q = float(target_total_quantity)
            # Clamp requested target to the total available stock so we never
            # create an impossible hard constraint that the model cannot satisfy.
            avail_total = sum(s.local_balance for s in remaining_stocks)
            req_q = min(tgt_q, avail_total)
            # Require at least the requested (clamped) quantity. This prevents
            # the solver from returning a tiny total just to reduce absolute error.
            prob += total_qty >= req_q

            error_total = LpVariable("error_total", lowBound=0)
            prob += error_total >= total_qty - tgt_q
            prob += error_total >= -(total_qty - tgt_q)
            objective_terms.append(error_total)
        except Exception:
            pass

    # If requested, prefer smaller total quantity as tie-breaker
    if minimize_quantity:
        try:
            small_weight = 1e-3
            objective_terms.append(small_weight * total_qty)
        except Exception:
            objective_terms.append(total_qty)

    prob += lpSum(objective_terms)
    
    # At least one stock must be selected
    prob += lpSum(stock_vars[s.id] for s in remaining_stocks) >= 1
    
    # Solve with time limit and relative gap to avoid long blocking calls
    time_limit = 10
    gap_rel = 0.01
    solver_name = 'CBC'
    cbc_msg = int(os.environ.get('OPTIMIZER_CBC_MSG', '0') or 0)

    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        except Exception:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_for_average_quality: solver %s elapsed=%.4f seconds (failed)", label, elapsed)
            except Exception:
                pass
            raise
        else:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_for_average_quality: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    # Log debug info about the constructed model before solving
    try:
        try:
            avail_total = sum(s.local_balance for s in remaining_stocks)
        except Exception:
            avail_total = None
    except Exception:
        avail_total = None
    try:
        logger.info("select_stocks_for_average_quality: dbg rows=%d avail_total=%s vars=%d constraints=%d", len(remaining_stocks), avail_total, len(prob.variables()), len(prob.constraints))
    except Exception:
        pass

    try:
        base_args = {'msg': cbc_msg, 'timeLimit': time_limit}
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'fracGap': gap_rel})), 'CBC')
        except TypeError:
            try:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'ratioGap': gap_rel})), 'CBC')
            except Exception:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**base_args)), 'CBC')
    except Exception as e:
        logger.exception("select_stocks_for_average_quality: CBC solver failed (%s)", e)
        return [], 0, 0.0

    from pulp import LpStatus, value
    try:
        logger.info("select_stocks_for_average_quality: solver used=%s status=%s objective=%s", solver_name, LpStatus[prob.status], value(prob.objective) if prob.status is not None else None)
    except Exception:
        logger.info("select_stocks_for_average_quality: solver used=%s (could not read status/objective)", solver_name)

    selected_ids = [s_id for s_id, var in stock_vars.items() if var.value() == 1]
    if not selected_ids:
        try:
            solver_status = LpStatus[prob.status] if prob.status is not None else 'Unknown'
        except Exception:
            solver_status = str(getattr(prob, 'status', 'Unknown'))
        logger.warning("select_stocks_for_average_quality: solver status=%s; no selection produced", solver_status)
        return [], 0, 0.0

    selected_stocks = CassiteriteStock.query.filter(
        CassiteriteStock.id.in_(selected_ids),
        CassiteriteStock.is_deleted.is_(False),
    ).all()
    total_unit_val = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(CassiteriteStock.id.in_(selected_ids), CassiteriteStock.is_deleted.is_(False)).scalar() or 0
    total_qty_val = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(CassiteriteStock.id.in_(selected_ids), CassiteriteStock.is_deleted.is_(False)).scalar() or 0
    achieved_moyenne = (total_unit_val / total_qty_val) if total_qty_val > 0 else 0
    return selected_stocks, achieved_moyenne, float(total_qty_val)


@trace_time
def select_stocks_with_minimum_quantities_cassiterite(target_moyenne=None, minimum_quantities=None, target_total_quantity=None):
    """
    Hybrid selection: BINARY for unrestricted stocks, CONTINUOUS for user-specified quantities.
    
    User specifies: "I want 150kg from stock S1, 80kg from S2, and optimize the rest"
    
    Used in STEP 3 of optimization (recalculate with user adjustments).
    
    Args:
        target_moyenne: Target average quality %
        minimum_quantities: Dict {stock_id: kg} of user-specified quantities
                           Stocks with values: CONTINUOUS (can have decimals)
                           Stocks without values: BINARY (0 or all available)
        target_total_quantity: Optional target total quantity (kg) to achieve
    
    Returns:
        (selected_stocks_list, achieved_moyenne, quantities_dict)
    """
    rows = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.local_balance,
        CassiteriteStock.unit_percent,
    ).filter(CassiteriteStock.local_balance > 0, CassiteriteStock.is_deleted.is_(False)).all()

    if not rows:
        return [], 0, {}

    remaining_stocks = [SimpleNamespace(id=r[0], local_balance=float(r[1] or 0), unit_percent=float(r[2] or 0)) for r in rows]

    stock_vars = {}
    for s in remaining_stocks:
        if minimum_quantities and s.id in minimum_quantities:
            try:
                min_qty = float(minimum_quantities[s.id])
            except Exception:
                min_qty = 0.0
            if min_qty < 0:
                min_qty = 0.0
            max_qty = float(s.local_balance)
            if min_qty > max_qty:
                # Clamp requested minimum to available quantity
                min_qty = max_qty
            stock_vars[s.id] = LpVariable(
                f"stock{s.id}",
                lowBound=min_qty,
                upBound=min_qty,
                cat=LpContinuous,
            )
        else:
            stock_vars[s.id] = LpVariable(f"stock{s.id}", cat=LpBinary)
    
    prob = LpProblem("Cassiterite_Stock_Selection_Hybrid", LpMinimize)
    
    # Calculate totals
    total_unit = lpSum(
        s.unit_percent / s.local_balance * (
            stock_vars[s.id] if (minimum_quantities and s.id in minimum_quantities)
            else stock_vars[s.id] * s.local_balance
        )
        for s in remaining_stocks if s.local_balance > 0
    )
    
    total_qty = lpSum(
        (
            stock_vars[s.id] if (minimum_quantities and s.id in minimum_quantities)
            else stock_vars[s.id] * s.local_balance
        )
        for s in remaining_stocks
    )
    
    # Objective: minimize error from target moyenne
    # target_moyenne is entered as percentage (e.g., 69.0 for 69%)
    # but total_unit is in decimal units, so scale target by /100
    objective_terms = []
    
    if target_moyenne is not None:
        target_moyenne_scaled = target_moyenne / 100.0
        error = LpVariable("error_moyenne", lowBound=0)
        prob += error >= total_unit - (target_moyenne_scaled * total_qty)
        prob += error >= -(total_unit - (target_moyenne_scaled * total_qty))
        objective_terms.append(error)
    
    # Optional: minimize quantity error when a target_total_quantity is provided
    if target_total_quantity is not None:
        try:
            tgt_q = float(target_total_quantity)
            error_total = LpVariable("error_total", lowBound=0)
            prob += error_total >= total_qty - tgt_q
            prob += error_total >= -(total_qty - tgt_q)
            objective_terms.append(error_total)
        except Exception:
            pass
    
    # Set objective
    if objective_terms:
        prob += lpSum(objective_terms)
    else:
        prob += total_qty
    
    # Total quantity must be positive
    prob += total_qty >= 1
    
    # Solve with time limit and relative gap to prevent long blocking solves
    time_limit = 10
    gap_rel = 0.01
    solver_name = 'CBC'
    cbc_msg = int(os.environ.get('OPTIMIZER_CBC_MSG', '0') or 0)

    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        except Exception:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_with_minimum_quantities_cassiterite: solver %s elapsed=%.4f seconds (failed)", label, elapsed)
            except Exception:
                pass
            raise
        else:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_with_minimum_quantities_cassiterite: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    # Debug info about the model
    try:
        try:
            avail_total = sum(s.local_balance for s in remaining_stocks)
        except Exception:
            avail_total = None
    except Exception:
        avail_total = None
    try:
        logger.info("select_stocks_with_minimum_quantities_cassiterite: dbg rows=%d avail_total=%s vars=%d constraints=%d", len(remaining_stocks), avail_total, len(prob.variables()), len(prob.constraints))
    except Exception:
        pass

    try:
        base_args = {'msg': cbc_msg, 'timeLimit': time_limit}
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'fracGap': gap_rel})), 'CBC')
        except TypeError:
            try:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'ratioGap': gap_rel})), 'CBC')
            except Exception:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**base_args)), 'CBC')
    except Exception as e:
        logger.exception("select_stocks_with_minimum_quantities_cassiterite: CBC solver failed (%s)", e)
        return [], 0, {}

    from pulp import LpStatus, value
    try:
        logger.info("select_stocks_with_minimum_quantities_cassiterite: solver used=%s status=%s objective=%s", solver_name, LpStatus[prob.status], value(prob.objective))
    except Exception:
        logger.info("select_stocks_with_minimum_quantities_cassiterite: solver finished (could not read status/objective)")
    
    # Extract results
    selected_stocks = []
    quantities = {}
    
    for s in remaining_stocks:
        var_value = stock_vars[s.id].value()
        if var_value is None:
            continue
        if minimum_quantities and s.id in minimum_quantities:
            qty = var_value
        else:
            qty = var_value * s.local_balance
        if qty and qty > 0.01:
            quantities[s.id] = qty

    selected_ids = list(quantities.keys())
    # Rehydrate only needed columns and compute totals using quantities dict
    rows = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.unit_percent,
        CassiteriteStock.local_balance,
    ).filter(
        CassiteriteStock.id.in_(selected_ids),
        CassiteriteStock.is_deleted.is_(False),
    ).all()
    row_map = {r[0]: {'unit_percent': float(r[1] or 0), 'local_balance': float(r[2] or 0)} for r in rows}

    total_unit_val = 0.0
    total_qty_val = 0.0
    for sid, qty in quantities.items():
        meta = row_map.get(sid)
        if not meta:
            continue
        lb = meta['local_balance']
        if lb > 0:
            total_unit_val += (meta['unit_percent'] / lb) * qty
            total_qty_val += qty

    achieved_moyenne = (total_unit_val / total_qty_val) if total_qty_val > 0 else 0
    selected_stocks = CassiteriteStock.query.filter(
        CassiteriteStock.id.in_(selected_ids),
        CassiteriteStock.is_deleted.is_(False),
    ).all()
    return selected_stocks, achieved_moyenne, quantities
