"""
Cassiterite Optimization Functions
Follows the same hybrid binary/continuous pattern as copper
"""
from pulp import LpProblem, LpVariable, lpSum, LpMinimize, LpBinary, LpContinuous, PULP_CBC_CMD
from cassiterite.models import CassiteriteStock
from types import SimpleNamespace
from config import db
import logging
from utils import trace_time
from sqlalchemy import func

logger = logging.getLogger(__name__)


@trace_time
def select_stocks_for_average_quality(target_moyenne=None, minimize_quantity=False):
    """
    Binary selection: Select stocks to achieve target average quality.
    Each stock is either selected (1) or not (0) - takes ALL available if selected.
    
    Used in STEP 1 of optimization.
    
    Args:
        target_moyenne: Target average purity/quality %
    
    Returns:
        (selected_stocks_list, achieved_moyenne)
    """
    rows = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.unit_percent,
        CassiteriteStock.local_balance,
    ).filter(CassiteriteStock.local_balance > 0).all()

    if not rows:
        return [], 0

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
    objective_terms = []
    if target_moyenne is not None:
        error = LpVariable("error_moyenne", lowBound=0)
        prob += error >= total_unit - (target_moyenne * total_qty)
        prob += error >= -(total_unit - (target_moyenne * total_qty))
        objective_terms.append(error)
    else:
        # If no target, prefer smaller total_qty (minimize quantity) or prefer higher quality?
        # Default behavior: minimize total_qty to keep selection compact.
        objective_terms.append(total_qty)

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
    time_limit = 12
    gap_rel = 0.01
    try:
        prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel))
    except TypeError:
        try:
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, ratioGap=gap_rel))
        except Exception:
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit))
    
    selected_ids = [s_id for s_id, var in stock_vars.items() if var.value() == 1]
    if not selected_ids:
        return [], 0
    if not selected_ids:
        return [], 0

    selected_stocks = CassiteriteStock.query.filter(CassiteriteStock.id.in_(selected_ids)).all()
    total_unit_val = db.session.query(func.coalesce(func.sum(CassiteriteStock.unit_percent), 0)).filter(CassiteriteStock.id.in_(selected_ids)).scalar() or 0
    total_qty_val = db.session.query(func.coalesce(func.sum(CassiteriteStock.local_balance), 0)).filter(CassiteriteStock.id.in_(selected_ids)).scalar() or 0
    achieved_moyenne = (total_unit_val / total_qty_val) if total_qty_val > 0 else 0
    return selected_stocks, achieved_moyenne, float(total_qty_val)


def select_stocks_with_minimum_quantities_cassiterite(target_moyenne=None, minimum_quantities=None):
    """
    Hybrid selection: BINARY for unrestricted stocks, CONTINUOUS for user-specified quantities.
    
    User specifies: "I want 150kg from stock S1, 80kg from S2, and optimize the rest"
    
    Used in STEP 3 of optimization (recalculate with user adjustments).
    
    Args:
        target_moyenne: Target average quality %
        minimum_quantities: Dict {stock_id: kg} of user-specified quantities
                           Stocks with values: CONTINUOUS (can have decimals)
                           Stocks without values: BINARY (0 or all available)
    
    Returns:
        (selected_stocks_list, achieved_moyenne, quantities_dict)
    """
    rows = db.session.query(
        CassiteriteStock.id,
        CassiteriteStock.local_balance,
        CassiteriteStock.unit_percent,
    ).filter(CassiteriteStock.local_balance > 0).all()

    if not rows:
        return [], 0, {}

    remaining_stocks = [SimpleNamespace(id=r[0], local_balance=float(r[1] or 0), unit_percent=float(r[2] or 0)) for r in rows]

    stock_vars = {}
    for s in remaining_stocks:
        if minimum_quantities and s.id in minimum_quantities:
            min_qty = minimum_quantities[s.id]
            stock_vars[s.id] = LpVariable(
                f"stock{s.id}",
                lowBound=min_qty,
                upBound=min(min_qty, s.local_balance) if s.local_balance else min_qty,
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
    if target_moyenne is not None:
        error = LpVariable("error_moyenne", lowBound=0)
        prob += error >= total_unit - (target_moyenne * total_qty)
        prob += error >= -(total_unit - (target_moyenne * total_qty))
        prob += error
    else:
        prob += total_qty
    
    # Total quantity must be positive
    prob += total_qty >= 1
    
    # Solve with time limit and relative gap to prevent long blocking solves
    time_limit = 12
    gap_rel = 0.01
    try:
        prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel))
    except TypeError:
        try:
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, ratioGap=gap_rel))
        except Exception:
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit))
    from pulp import LpStatus, value
    try:
        logger.info("select_stocks_with_minimum_quantities_cassiterite: solver status=%s objective=%s", LpStatus[prob.status], value(prob.objective))
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
    rows = db.session.query(CassiteriteStock.id, CassiteriteStock.unit_percent, CassiteriteStock.local_balance).filter(CassiteriteStock.id.in_(selected_ids)).all()
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
    selected_stocks = CassiteriteStock.query.filter(CassiteriteStock.id.in_(selected_ids)).all()
    return selected_stocks, achieved_moyenne, quantities
