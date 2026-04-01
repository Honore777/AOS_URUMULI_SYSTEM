from pulp import LpProblem, LpVariable, lpSum, LpMinimize, LpBinary, LpContinuous, PULP_CBC_CMD
from copper.models import CopperStock
from sqlalchemy import func
from types import SimpleNamespace
from config import db
import logging
from utils import trace_time

logger = logging.getLogger(__name__)

@trace_time
def select_stocks_for_moyenne(target_moyenne=None, target_moyenne_nb=None):
    """
    Original function: Binary selection (all or nothing per stock)
    Used for initial auto-filtering
    """
    rows = db.session.query(
        CopperStock.id,
        CopperStock.unit_percent,
        CopperStock.local_balance,
        CopperStock.t_unity,
    ).filter(CopperStock.local_balance > 0).all()

    if not rows:
        return [], 0, 0

    remaining_stocks = [SimpleNamespace(id=r[0], unit_percent=float(r[1] or 0), local_balance=float(r[2] or 0), t_unity=float(r[3] or 0)) for r in rows]
    stock_vars = {s.id: LpVariable(f"stock{s.id}", cat=LpBinary) for s in remaining_stocks}

    prob = LpProblem("Stock_selection_for_Target_Moyenne", LpMinimize)

    # Calculate totals based on selected stocks
    total_unit_percent = lpSum(s.unit_percent * stock_vars[s.id] for s in remaining_stocks)
    total_t_unity = lpSum(s.t_unity * stock_vars[s.id] for s in remaining_stocks)
    total_balance = lpSum(s.local_balance * stock_vars[s.id] for s in remaining_stocks)

    # Objective: minimize absolute difference(s)
    objective_terms = []

    if target_moyenne:
        error_moyenne = LpVariable("error_moyenne", lowBound=0)
        prob += error_moyenne >= total_unit_percent - target_moyenne * total_balance
        prob += error_moyenne >= -(total_unit_percent - target_moyenne * total_balance)
        objective_terms.append(error_moyenne)

    if target_moyenne_nb:
        error_moyenne_nb = LpVariable("error_moyenne_nb", lowBound=0)
        prob += error_moyenne_nb >= total_t_unity - target_moyenne_nb * total_balance
        prob += error_moyenne_nb >= -(total_t_unity - target_moyenne_nb * total_balance)
        objective_terms.append(error_moyenne_nb)

    # Objective: minimize total error
    prob += lpSum(objective_terms)

    # Constraint: at least one stock must be selected
    prob += lpSum(stock_vars[s.id] for s in remaining_stocks) >= 1

    # Solve with a time limit and relative gap to avoid long blocking calls
    # time_limit: seconds solver will run (10-12s suggested)
    # gap_rel: relative optimality gap (e.g., 0.01 = 1%)
    time_limit = 12
    gap_rel = 0.01
    try:
        prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel))
    except TypeError:
        # Some pulp versions use different parameter name for gap; try ratioGap
        try:
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, ratioGap=gap_rel))
        except Exception:
            # Fallback: time limit only
            prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit))

    selected_ids = [s_id for s_id, var in stock_vars.items() if var.value() == 1]

    if not selected_ids:
        return [], 0, 0

    # Rehydrate selected stocks ORM objects (for display) but compute aggregates using DB
    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(selected_ids)).all()

    # Use DB aggregates for totals to avoid Python-side full-table sums
    total_unit = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0
    total_tunity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0
    total_balance_val = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0

    achieved_moyenne = (total_unit / total_balance_val) if total_balance_val else 0
    achieved_moyenne_nb = (total_tunity / total_balance_val) if total_balance_val else 0

    return selected_stocks, achieved_moyenne, achieved_moyenne_nb


def select_stocks_with_minimum_quantities(target_moyenne=None, target_moyenne_nb=None, minimum_quantities=None):
    """
    Advanced function: HYBRID selection (mix of binary and continuous)
    
    User says: "I want moyenne=45, BUT use at least 150kg from S1"
    
    This function RE-OPTIMIZES ALL stocks while respecting:
    1. The target moyenne (quality constraint)
    2. User's specified minimum quantities (continuous - may have decimals)
    3. Other stocks are BINARY (0 = don't use, 1 = use ALL available)
       → NO unnecessary decimals like 0.123kg or 1.945kg
    
    Args:
        target_moyenne: Target quality %
        target_moyenne_nb: Target secondary quality metric
        minimum_quantities: Dict {stock_id: minimum_kg}
                           Example: {1: 150, 2: 80}
                           Stocks WITH minimums: can have decimals (user specified)
                           Stocks WITHOUT minimums: binary only (0 or all, NO decimals)
    
    Returns:
        (selected_stocks_list, achieved_moyenne, achieved_moyenne_nb, quantities_dict)
    """
    # Load only the columns needed for LP; rehydrate selected ORM objects later
    rows = db.session.query(
        CopperStock.id,
        CopperStock.local_balance,
        CopperStock.unit_percent,
        CopperStock.t_unity,
    ).filter(CopperStock.local_balance > 0).all()

    if not rows:
        return [], 0, 0, {}

    remaining_stocks = [SimpleNamespace(id=r[0], local_balance=float(r[1] or 0), unit_percent=float(r[2] or 0), t_unity=float(r[3] or 0)) for r in rows]

    # HYBRID: mix continuous and binary variables
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
    
    prob = LpProblem("Stock_selection_with_minimums_hybrid", LpMinimize)
    
    # ===== Calculate totals BASED ON quantities PuLP chooses =====
    # Extract percentage first: percentage = unit_percent / local_balance
    # For continuous variables: percentage × user_qty
    # For binary variables: percentage × (0_or_1 × local_balance)
    total_unit_percent = lpSum(
        (s.unit_percent / s.local_balance if s.local_balance > 0 else 0) * (
            stock_vars[s.id] if (minimum_quantities and s.id in minimum_quantities)
            else stock_vars[s.id] * s.local_balance
        )
        for s in remaining_stocks
    )
    
    total_t_unity = lpSum(
        (s.t_unity / s.local_balance if s.local_balance > 0 else 0) * (
            stock_vars[s.id] if (minimum_quantities and s.id in minimum_quantities)
            else stock_vars[s.id] * s.local_balance
        )
        for s in remaining_stocks
    )
    
    total_balance = lpSum(
        (
            stock_vars[s.id] if (minimum_quantities and s.id in minimum_quantities)
            else stock_vars[s.id] * s.local_balance
        )
        for s in remaining_stocks
    )
    
    # ===== SAME ERROR MINIMIZATION AS ORIGINAL =====
    objective_terms = []
    
    if target_moyenne:
        error_moyenne = LpVariable("error_moyenne", lowBound=0)
        prob += error_moyenne >= total_unit_percent - target_moyenne * total_balance
        prob += error_moyenne >= -(total_unit_percent - target_moyenne * total_balance)
        objective_terms.append(error_moyenne)
    
    if target_moyenne_nb:
        error_moyenne_nb = LpVariable("error_moyenne_nb", lowBound=0)
        prob += error_moyenne_nb >= total_t_unity - target_moyenne_nb * total_balance
        prob += error_moyenne_nb >= -(total_t_unity - target_moyenne_nb * total_balance)
        objective_terms.append(error_moyenne_nb)
    
    # ===== CONSTRAINT: Total quantity must be positive =====
    prob += total_balance >= 1
    
    # ===== OBJECTIVE: Minimize error =====
    if objective_terms:
        prob += lpSum(objective_terms)
    else:
        # If no target specified, just minimize total used (prefer smaller quantities)
        prob += total_balance
    
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
        logger.info("select_stocks_with_minimum_quantities: solver status=%s objective=%s", LpStatus[prob.status], value(prob.objective))
    except Exception:
        logger.info("select_stocks_with_minimum_quantities: solver finished (could not read status/objective)")
    
    # ===== Extract results =====
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
    # Rehydrate only needed columns to compute achieved metrics
    rows = db.session.query(CopperStock.id, CopperStock.unit_percent, CopperStock.t_unity, CopperStock.local_balance).filter(CopperStock.id.in_(selected_ids)).all()
    # Map by id for quick lookup
    row_map = {r[0]: {'unit_percent': float(r[1] or 0), 't_unity': float(r[2] or 0), 'local_balance': float(r[3] or 0)} for r in rows}

    total_unit = 0.0
    total_tunity = 0.0
    total_qty = 0.0
    for sid, qty in quantities.items():
        meta = row_map.get(sid)
        if not meta:
            continue
        lb = meta['local_balance']
        if lb > 0:
            total_unit += (meta['unit_percent'] / lb) * qty
            total_tunity += (meta['t_unity'] / lb) * qty
            total_qty += qty

    achieved_moyenne = (total_unit / total_qty) if total_qty else 0
    achieved_moyenne_nb = (total_tunity / total_qty) if total_qty else 0

    # Rehydrate ORM objects for display
    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(selected_ids)).all()
    return selected_stocks, achieved_moyenne, achieved_moyenne_nb, quantities
