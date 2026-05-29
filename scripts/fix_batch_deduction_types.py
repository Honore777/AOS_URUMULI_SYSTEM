"""
Migration to fix batch_deduction.batch_id type issues

ISSUE: Some batch_deduction records have batch_id as string (e.g., 'batch_20260518_37e140')
       when they should be integers (FK to bulk_output_plan.id)

SOLUTION: 
1. Delete batch_deduction records with invalid batch_id (string, not matching any plan.id)
2. Add CHECK constraint to prevent future issues
3. Add code protection in management.py
"""

import logging
from config import db
from core.models import BulkOutputPlan, BatchDeduction

logger = logging.getLogger(__name__)

def clean_batch_deduction_batch_ids():
    """Clean up batch_deduction records with invalid batch_id values."""
    
    # Get all batch_deduction records
    all_deductions = BatchDeduction.query.all()
    valid_plan_ids = {p.id for p in BulkOutputPlan.query.all()}
    
    deleted_count = 0
    kept_count = 0
    
    for deduction in all_deductions:
        # Check if batch_id is a valid FK
        if deduction.batch_id not in valid_plan_ids:
            logger.warning(
                f"Deleting invalid BatchDeduction {deduction.id}: "
                f"batch_id={deduction.batch_id} not found in bulk_output_plan"
            )
            db.session.delete(deduction)
            deleted_count += 1
        else:
            kept_count += 1
    
    db.session.commit()
    logger.info(f"Cleaned batch_deduction: deleted {deleted_count}, kept {kept_count}")
    return deleted_count, kept_count

def safe_batch_deduction_query(batch_id_input):
    """
    Safely query BatchDeduction by batch_id.
    Handles both string batch_id (BulkOutputPlan.batch_id) and integer batch_id (FK).
    
    Args:
        batch_id_input: Either string (batch_id) or integer (plan.id)
    
    Returns:
        sum of amount_rwf or 0.0
    """
    try:
        # If input is string, convert to plan.ids first
        if isinstance(batch_id_input, str):
            plans = BulkOutputPlan.query.filter(
                BulkOutputPlan.batch_id == batch_id_input
            ).all()
            plan_ids = [p.id for p in plans]
            
            if not plan_ids:
                return 0.0
            
            total = (
                db.session.query(func.coalesce(func.sum(BatchDeduction.amount_rwf), 0))
                .filter(BatchDeduction.batch_id.in_(plan_ids))
                .scalar() or 0.0
            )
        else:
            # Input is already integer (plan.id)
            total = (
                db.session.query(func.coalesce(func.sum(BatchDeduction.amount_rwf), 0))
                .filter(BatchDeduction.batch_id == batch_id_input)
                .scalar() or 0.0
            )
        
        return float(total or 0.0)
    except Exception as e:
        logger.exception(f"Error querying BatchDeduction for batch_id={batch_id_input}: {e}")
        return 0.0

# Usage in management.py _batch_outstanding_rwf:
# Replace:
#   total_deducted = (
#       db.session.query(func.coalesce(func.sum(BatchDeduction.amount_rwf), 0))
#       .filter(BatchDeduction.batch_id.in_(plan_ids))
#       .scalar() or 0.0
#   )
# With:
#   total_deducted = safe_batch_deduction_query(batch_id)

