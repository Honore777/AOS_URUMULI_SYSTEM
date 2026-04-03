from pulp import LpProblem, LpVariable, lpSum, LpMinimize, LpBinary, LpContinuous, PULP_CBC_CMD
# Prefer HiGHS solver if available; fall back to CBC via PULP_CBC_CMD
try:
    from pulp import HiGHS_CMD
    HAS_HIGHS = True
except Exception:
    HiGHS_CMD = None
    HAS_HIGHS = False

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

    # Minimize quantity error when a target_total_quantity is provided
    if target_total_quantity is not None:
        try:
            tgt_q = float(target_total_quantity)
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
    # time_limit: seconds solver will run (10-12s suggested)
    # gap_rel: relative optimality gap (e.g., 0.01 = 1%)
    time_limit = 12
    gap_rel = 0.01
    # Choose solver and log decision for diagnostics
    solver_name = 'CBC'
    solver_path = None
    # Wrap solver invocation to measure elapsed time and log it. If HiGHS
    # takes longer than the requested time_limit, we log a warning so
    # operators can decide whether to prefer CBC in deployments.
    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        finally:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_for_moyenne: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    try:
        if HAS_HIGHS and HiGHS_CMD is not None:
            highs_exec = _find_highs_executable()
            if highs_exec:
                solver_path = highs_exec
                solver_name = 'HiGHS'
                logger.info("select_stocks_for_moyenne: using HiGHS at %s", highs_exec)
                try:
                    elapsed = _run_solver_and_time(lambda: prob.solve(HiGHS_CMD(path=highs_exec, msg=0, timeLimit=time_limit)), 'HiGHS')
                    if elapsed > max(1.0, time_limit * 1.05):
                        logger.warning("select_stocks_for_moyenne: HiGHS exceeded time_limit (requested=%ss, elapsed=%.3fs)", time_limit, elapsed)
                except Exception as e:
                    logger.warning("select_stocks_for_moyenne: HiGHS execution failed (%s), falling back to CBC", e)
                    _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
            else:
                logger.info("select_stocks_for_moyenne: HiGHS detected in pulp but highs executable not found; using CBC")
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
        else:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
    except TypeError:
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, ratioGap=gap_rel)), 'CBC')
        except Exception:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit)), 'CBC')
    from pulp import LpStatus, value
    try:
        logger.info("select_stocks_for_moyenne: solver used=%s path=%s status=%s objective=%s",
                    solver_name, solver_path, LpStatus[prob.status], value(prob.objective) if prob.status is not None else None)
    except Exception:
        logger.info("select_stocks_for_moyenne: solver used=%s path=%s (could not read status/objective)", solver_name, solver_path)

    selected_ids = [s_id for s_id, var in stock_vars.items() if var.value() == 1]

    # If the solver failed to produce a selection (or returned infeasible),
    # fall back to a lightweight greedy heuristic so the UI can show a
    # reasonable recommendation instead of an empty result. This keeps the
    # interactive flow usable when the chosen LP solver is not available
    # or when the model is not solvable by the exact solver within limits.
    if not selected_ids:
        try:
            logger.info("select_stocks_for_moyenne: solver produced no selection; using greedy fallback")
            # Sort by per-kg quality (unit_percent per local_balance) desc
            sorted_stocks = sorted(remaining_stocks, key=lambda s: (s.unit_percent / s.local_balance) if s.local_balance > 0 else 0, reverse=True)
            sel_ids = []
            sum_unit = 0.0
            sum_balance = 0.0
            tgt_m = None
            try:
                tgt_m = float(target_moyenne) if target_moyenne is not None else None
            except Exception:
                tgt_m = None
            tgt_q = None
            try:
                tgt_q = float(target_total_quantity) if target_total_quantity is not None else None
            except Exception:
                tgt_q = None

            for s in sorted_stocks:
                sel_ids.append(s.id)
                sum_unit += s.unit_percent
                sum_balance += s.local_balance
                achieved = (sum_unit / sum_balance) if sum_balance else 0
                # stop if we reached target moyenne (if provided)
                if tgt_m is not None and achieved >= tgt_m:
                    break
                # or stop if we reached target total quantity (if provided)
                if tgt_q is not None and sum_balance >= tgt_q:
                    break

            # ensure at least one stock selected
            if not sel_ids and remaining_stocks:
                sel_ids = [remaining_stocks[0].id]

            selected_ids = sel_ids
        except Exception:
            # fallback failed — return safe empty values
            logger.exception("select_stocks_for_moyenne: greedy fallback failed")
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
    time_limit = 12
    gap_rel = 0.01
    # Choose solver and log decision for diagnostics
    solver_name = 'CBC'
    solver_path = None
    def _run_solver_and_time(solver_callable, label):
        start = time.perf_counter()
        try:
            solver_callable()
        finally:
            elapsed = time.perf_counter() - start
            try:
                logger.info("select_stocks_with_minimum_quantities: solver %s elapsed=%.4f seconds", label, elapsed)
            except Exception:
                pass
            return elapsed

    try:
        if HAS_HIGHS and HiGHS_CMD is not None:
            highs_exec = _find_highs_executable()
            if highs_exec:
                solver_path = highs_exec
                solver_name = 'HiGHS'
                logger.info("select_stocks_with_minimum_quantities: using HiGHS at %s", highs_exec)
                try:
                    elapsed = _run_solver_and_time(lambda: prob.solve(HiGHS_CMD(path=highs_exec, msg=0, timeLimit=time_limit)), 'HiGHS')
                    if elapsed > max(1.0, time_limit * 1.05):
                        logger.warning("select_stocks_with_minimum_quantities: HiGHS exceeded time_limit (requested=%ss, elapsed=%.3fs)", time_limit, elapsed)
                except Exception as e:
                    logger.warning("select_stocks_with_minimum_quantities: HiGHS execution failed (%s), falling back to CBC", e)
                    _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
            else:
                logger.info("select_stocks_with_minimum_quantities: HiGHS detected in pulp but highs executable not found; using CBC")
                _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
        else:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, fracGap=gap_rel)), 'CBC')
    except TypeError:
        try:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit, ratioGap=gap_rel)), 'CBC')
        except Exception:
            _run_solver_and_time(lambda: prob.solve(PULP_CBC_CMD(msg=0, timeLimit=time_limit)), 'CBC')
    logger.info("select_stocks_with_minimum_quantities: solver used=%s path=%s", solver_name, solver_path)
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
