from pulp import LpProblem, LpVariable, lpSum, LpMinimize, LpBinary, LpContinuous, PULP_CBC_CMD
# Prefer HiGHS solver if available; fall back to CBC via PULP_CBC_CMD


import shutil, os, sys
import time
from threading import Lock

def _find_highs_executable():
    """Return path to highs executable if available, else None.

    Checks PATH first via shutil.which('highs'), then looks in common
    conda env locations (sys.prefix/Library/bin/highs.exe on Windows).
    """
    # First try PATH (cross-platform). Check both unix and windows executable names.
    which = shutil.which('highs') or shutil.which('highs.exe')
    if which:
        return which

    # Next check common virtualenv/conda locations and system locations.
    prefix = sys.prefix
    candidates = [
        os.path.join(prefix, 'Library', 'bin', 'highs.exe'),  # conda on Windows
        os.path.join(prefix, 'Scripts', 'highs.exe'),        # venv on Windows
        os.path.join(prefix, 'bin', 'highs'),                # venv/virtualenv on Unix
        os.path.join('/usr', 'local', 'bin', 'highs'),        # common system location
        os.path.join('/usr', 'bin', 'highs'),                # fallback system location
    ]

    for candidate in candidates:
        try:
            if os.path.exists(candidate):
                return candidate
        except Exception:
            continue

    return None
from sqlalchemy import func
from types import SimpleNamespace
from config import db
import logging
from utils import trace_time

logger = logging.getLogger(__name__)

# Toggle to allow falling back to CBC when HiGHS executable is missing.
# Default is disabled to avoid silent, misleading fallbacks in deployed
# environments. Set OPTIMIZER_ALLOW_FALLBACK_TO_CBC=1 to re-enable.
import os
ALLOW_FALLBACK_CBC = os.environ.get('OPTIMIZER_ALLOW_FALLBACK_TO_CBC', '0').lower() in ('1', 'true', 'yes')

# Simple in-process cache for optimizer results to avoid repeated solves
# Keyed by function name + parameters. Stores lightweight serializable
# results (ids, numeric aggregates) and rehydrates ORM objects on hit.
_OPT_CACHE = {}
_OPT_CACHE_LOCK = Lock()

def _cache_get(key, ttl=60):
    try:
        with _OPT_CACHE_LOCK:
            entry = _OPT_CACHE.get(key)
            if not entry:
                return None
            ts = entry.get('ts', 0)
            if (time.time() - ts) > entry.get('ttl', ttl):
                # expired
                _OPT_CACHE.pop(key, None)
                return None
            return entry.get('data')
    except Exception:
        return None

def _cache_set(key, data, ttl=60):
    try:
        with _OPT_CACHE_LOCK:
            _OPT_CACHE[key] = {'ts': time.time(), 'data': data, 'ttl': ttl}
    except Exception:
        pass

@trace_time
def select_stocks_for_moyenne(target_moyenne=None, target_moyenne_nb=None, target_total_quantity=None, minimize_quantity=False):
    """
    Original function: Binary selection (all or nothing per stock)
    Used for initial auto-filtering
    """
    from copper.models import CopperStock
    rows = db.session.query(
        CopperStock.id,
        CopperStock.unit_percent,
        CopperStock.local_balance,
        CopperStock.t_unity,
    ).filter(CopperStock.local_balance > 0).all()

    if not rows:
        return [], 0, 0, 0.0

    # Check cache to avoid repeated heavy solves for identical parameters
    try:
        cache_key = (
            'select_stocks_for_moyenne',
            repr(target_moyenne),
            repr(target_moyenne_nb),
            repr(target_total_quantity),
            str(bool(minimize_quantity)),
        )
        cached = _cache_get(cache_key, ttl=60)
        if cached:
            try:
                ids = cached.get('ids', [])
                if ids:
                    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(ids)).all()
                else:
                    selected_stocks = []
                return selected_stocks, float(cached.get('achieved_moyenne', 0)), float(cached.get('achieved_moyenne_nb', 0)), float(cached.get('total_balance', 0))
            except Exception:
                # Cache miss due to DB rehydration failure — continue to compute
                pass
    except Exception:
        pass

    remaining_stocks = [SimpleNamespace(id=r[0], unit_percent=float(r[1] or 0), local_balance=float(r[2] or 0), t_unity=float(r[3] or 0)) for r in rows]
    stock_vars = {s.id: LpVariable(f"stock{s.id}", cat=LpBinary) for s in remaining_stocks}

    prob = LpProblem("Stock_selection_for_Target_Moyenne", LpMinimize)

    # Calculate totals based on selected stocks
    total_unit_percent = lpSum(s.unit_percent * stock_vars[s.id] for s in remaining_stocks)
    total_t_unity = lpSum(s.t_unity * stock_vars[s.id] for s in remaining_stocks)
    total_balance = lpSum(s.local_balance * stock_vars[s.id] for s in remaining_stocks)

    # Objective: minimize absolute difference(s)
    objective_terms = []

    if target_moyenne is not None:
        error_moyenne = LpVariable("error_moyenne", lowBound=0)
        prob += error_moyenne >= total_unit_percent - target_moyenne * total_balance
        prob += error_moyenne >= -(total_unit_percent - target_moyenne * total_balance)
        objective_terms.append(error_moyenne)

    if target_moyenne_nb is not None:
        error_moyenne_nb = LpVariable("error_moyenne_nb", lowBound=0)
        prob += error_moyenne_nb >= total_t_unity - target_moyenne_nb * total_balance
        prob += error_moyenne_nb >= -(total_t_unity - target_moyenne_nb * total_balance)
        objective_terms.append(error_moyenne_nb)

    # Minimize quantity error when a target_total_quantity is provided.
    # Also enforce a clamped lower bound on total_balance to avoid the solver
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
            prob += total_balance >= req_q

            error_total = LpVariable("error_total", lowBound=0)
            prob += error_total >= total_balance - tgt_q
            prob += error_total >= -(total_balance - tgt_q)
            objective_terms.append(error_total)
        except Exception:
            pass

    # Objective: minimize total error
    # If requested, prefer solutions with smaller total quantity as a tie-breaker.
    # We add a small-weighted total_balance term so objective still prioritizes
    # quality error but prefers smaller totals when errors are equal.
    if minimize_quantity:
        try:
            small_weight = 1e-3
            objective_terms.append(small_weight * total_balance)
        except Exception:
            objective_terms.append(total_balance)

    prob += lpSum(objective_terms)

    # Constraint: at least one stock must be selected
    prob += lpSum(stock_vars[s.id] for s in remaining_stocks) >= 1

    # NOTE: do NOT enforce strict equality on total quantity here.
    # We use a soft objective term (`error_total`) above to allow
    # the solver to return the closest feasible selection instead
    # of producing an infeasible model when exact equality cannot
    # be achieved with binary-only choices.

    # Solve with a time limit and relative gap to avoid long blocking calls
    # time_limit: seconds solver will run (10s suggested)
    # gap_rel: relative optimality gap (e.g., 0.01 = 1%)
    time_limit = 10
    gap_rel = 0.01
    # We will use CBC exclusively in this deployment (HiGHS disabled)
    solver_name = 'CBC'
    # Keep solver invocation simple: use PuLP's bundled CBC (no explicit path)
    cbc_msg = int(os.environ.get('OPTIMIZER_CBC_MSG', '0') or 0)

    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        except Exception:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_for_moyenne: solver %s elapsed=%.4f seconds (failed)", label, elapsed)
            except Exception:
                pass
            raise
        else:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_for_moyenne: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    # Log some debug info about the constructed model before solving
    try:
        try:
            avail_total = sum(s.local_balance for s in remaining_stocks)
        except Exception:
            avail_total = None
    except Exception:
        avail_total = None
    try:
        logger.info("select_stocks_for_moyenne: dbg rows=%d avail_total=%s vars=%d constraints=%d", len(remaining_stocks), avail_total, len(prob.variables()), len(prob.constraints))
    except Exception:
        pass

    try:
        # Run CBC only. Try the modern `fracGap` parameter first, fall back to
        # `ratioGap` for older PuLP, then to no-gap argument. Pass detected
        # `path` when available and respect OPTIMIZER_CBC_MSG for verbosity.
        base_args = {'msg': cbc_msg, 'timeLimit': time_limit}
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'fracGap': gap_rel})), 'CBC')
        except TypeError:
            try:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'ratioGap': gap_rel})), 'CBC')
            except Exception:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**base_args)), 'CBC')
    except Exception as e:
        logger.exception("select_stocks_for_moyenne: CBC solver failed (%s)", e)
        return [], 0, 0, 0.0
    from pulp import LpStatus, value
    try:
        logger.info("select_stocks_for_moyenne: solver used=%s status=%s objective=%s",
                    solver_name, LpStatus[prob.status], value(prob.objective) if prob.status is not None else None)
    except Exception:
        logger.info("select_stocks_for_moyenne: solver used=%s (could not read status/objective)", solver_name)

    selected_ids = [s_id for s_id, var in stock_vars.items() if var.value() == 1]

    # If the solver produced no selection, do NOT apply a greedy heuristic
    # fallback. Return empty results so callers can detect solver failure and
    # avoid being presented with misleading heuristic recommendations.
    if not selected_ids:
        try:
            solver_status = LpStatus[prob.status] if prob.status is not None else 'Unknown'
        except Exception:
            solver_status = str(prob.status)
        logger.warning("select_stocks_for_moyenne: solver status=%s; no selection produced; not applying greedy fallback", solver_status)
        return [], 0, 0, 0.0

    # Rehydrate selected stocks ORM objects (for display) but compute aggregates using DB
    selected_stocks = CopperStock.query.filter(CopperStock.id.in_(selected_ids)).all()

    # Use DB aggregates for totals to avoid Python-side full-table sums
    total_unit = db.session.query(func.coalesce(func.sum(CopperStock.unit_percent), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0
    total_tunity = db.session.query(func.coalesce(func.sum(CopperStock.t_unity), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0
    total_balance_val = db.session.query(func.coalesce(func.sum(CopperStock.local_balance), 0)).filter(CopperStock.id.in_(selected_ids)).scalar() or 0

    achieved_moyenne = (total_unit / total_balance_val) if total_balance_val else 0
    achieved_moyenne_nb = (total_tunity / total_balance_val) if total_balance_val else 0

    # Return the achieved total quantity as well so callers can display it
    try:
        # Cache lightweight result (ids + aggregates) for short TTL to avoid
        # re-solving identical problems during quick UI navigation.
        try:
            selected_ids = [s.id for s in selected_stocks]
        except Exception:
            selected_ids = []
        cache_data = {
            'ids': selected_ids,
            'achieved_moyenne': float(achieved_moyenne),
            'achieved_moyenne_nb': float(achieved_moyenne_nb),
            'total_balance': float(total_balance_val),
        }
        _cache_set(cache_key, cache_data, ttl=60)
    except Exception:
        pass

    return selected_stocks, achieved_moyenne, achieved_moyenne_nb, float(total_balance_val)


def select_stocks_with_minimum_quantities(target_moyenne=None, target_moyenne_nb=None, minimum_quantities=None, target_total_quantity=None):
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
    from copper.models import CopperStock
    rows = db.session.query(
        CopperStock.id,
        CopperStock.local_balance,
        CopperStock.unit_percent,
        CopperStock.t_unity,
    ).filter(CopperStock.local_balance > 0).all()

    if not rows:
        return [], 0, 0, {}

    # Build a cache key that includes the minimum_quantities entries (sorted)
    try:
        min_tuple = tuple(sorted(((int(k), float(v)) for k, v in (minimum_quantities or {}).items()))) if minimum_quantities else ()
    except Exception:
        min_tuple = ()
    cache_key = ('select_stocks_with_minimum_quantities', repr(target_moyenne), repr(target_moyenne_nb), repr(target_total_quantity), repr(min_tuple))
    cached = _cache_get(cache_key, ttl=60)
    if cached:
        try:
            ids = cached.get('ids', [])
            quantities_cached = cached.get('quantities', {}) or {}
            selected_stocks = CopperStock.query.filter(CopperStock.id.in_(ids)).all() if ids else []
            return selected_stocks, float(cached.get('achieved_moyenne', 0)), float(cached.get('achieved_moyenne_nb', 0)), quantities_cached
        except Exception:
            pass

    remaining_stocks = [SimpleNamespace(id=r[0], local_balance=float(r[1] or 0), unit_percent=float(r[2] or 0), t_unity=float(r[3] or 0)) for r in rows]

    # HYBRID: mix continuous and binary variables
    stock_vars = {}
    for s in remaining_stocks:
        if minimum_quantities and s.id in minimum_quantities:
            # Allow a continuous variable between the user-specified minimum
            # and the available `local_balance` for that stock.
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
            # Preserve hybrid design: when the user provides a minimum_quantities
            # entry, treat it as a fixed quantity (lowBound == upBound) so PuLP
            # uses exactly that amount for this stock.
            stock_vars[s.id] = LpVariable(
                f"stock{s.id}",
                lowBound=min_qty,
                upBound=min_qty,
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
    
    if target_moyenne is not None:
        error_moyenne = LpVariable("error_moyenne", lowBound=0)
        prob += error_moyenne >= total_unit_percent - target_moyenne * total_balance
        prob += error_moyenne >= -(total_unit_percent - target_moyenne * total_balance)
        objective_terms.append(error_moyenne)
    
    if target_moyenne_nb is not None:
        error_moyenne_nb = LpVariable("error_moyenne_nb", lowBound=0)
        prob += error_moyenne_nb >= total_t_unity - target_moyenne_nb * total_balance
        prob += error_moyenne_nb >= -(total_t_unity - target_moyenne_nb * total_balance)
        objective_terms.append(error_moyenne_nb)
    
    # ===== CONSTRAINT: Total quantity must be positive =====
    prob += total_balance >= 1

    # Optional hard constraint: force total selected quantity to match target
    if target_total_quantity is not None:
        try:
            tgt_q = float(target_total_quantity)
            # Instead of strict equality, minimize its error via objective
            error_total = LpVariable("error_total", lowBound=0)
            prob += error_total >= total_balance - tgt_q
            prob += error_total >= -(total_balance - tgt_q)
            objective_terms.append(error_total)
        except Exception:
            pass
    
    # ===== OBJECTIVE: Minimize error =====
    if objective_terms:
        prob += lpSum(objective_terms)
    else:
        # If no target specified, just minimize total used (prefer smaller quantities)
        prob += total_balance
    
    # Solve with time limit and relative gap to prevent long blocking solves
    time_limit = 10
    gap_rel = 0.01
    # Choose solver and log decision for diagnostics
    solver_name = 'CBC'
    # Choose CBC via PuLP's default invocation (no explicit path)
    cbc_msg = int(os.environ.get('OPTIMIZER_CBC_MSG', '0') or 0)
    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        except Exception:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_with_minimum_quantities: solver %s elapsed=%.4f seconds (failed)", label, elapsed)
            except Exception:
                pass
            raise
        else:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_with_minimum_quantities: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    try:
        # Force CBC-only solving. Do not attempt to use HiGHS. Keep invocation
        # simple and rely on PuLP's bundled CBC executable.
        base_args = {'msg': cbc_msg, 'timeLimit': time_limit}
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'fracGap': gap_rel})), 'CBC')
        except TypeError:
            try:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**{**base_args, 'ratioGap': gap_rel})), 'CBC')
            except Exception:
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(**base_args)), 'CBC')
    except Exception as e:
        logger.exception("select_stocks_with_minimum_quantities: CBC solver failed (%s)", e)
        return [], 0, 0, {}
    logger.info("select_stocks_with_minimum_quantities: solver used=%s", solver_name)
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
    try:
        cache_data = {
            'ids': selected_ids,
            'quantities': quantities,
            'achieved_moyenne': float(achieved_moyenne),
            'achieved_moyenne_nb': float(achieved_moyenne_nb),
        }
        _cache_set(cache_key, cache_data, ttl=60)
    except Exception:
        pass

    return selected_stocks, achieved_moyenne, achieved_moyenne_nb, quantities
