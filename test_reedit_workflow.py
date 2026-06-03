#!/usr/bin/env python3
"""
Test Plan: Cassiterite Optimization Re-Edit Workflow

This test validates that:
1. Session properly stores quantities after recalculate
2. Re-edit GET request properly restores quantities from session
3. User can cycle through edit → recalculate → re-edit → recalculate multiple times
4. Achieved values are preserved throughout the workflow
"""

from app import app, db
from cassiterite.forms import OptimizeCassiteriteForm

def test_session_workflow():
    """
    Simulate the complete workflow:
    1. User enters target_moyenne=69.78 and target_qty=null
    2. Clicks "Filter Stocks" → Optimization runs → Shows result (mode=result)
    3. Clicks "Re-Edit Selection" → Gets quantities from session → Shows edit (mode=edit)
    4. Adjusts quantities and clicks "Recalculate" → Optimization runs → Shows result (mode=result)
    5. Clicks "Re-Edit Selection" again → Gets NEW quantities from session → Shows edit (mode=edit)
    """
    
    with app.test_client() as client:
        with client.session_transaction() as sess:
            # Clean slate
            for key in ['optimization_quantities', 'optimization_achieved_moyenne', 
                       'optimization_achieved_total_quantity', 'optimization_mode',
                       'optimization_target_moyenne', 'optimization_target_total_quantity']:
                sess.pop(key, None)
        
        print("\n" + "="*80)
        print("TEST: Re-Edit Workflow Session Persistence")
        print("="*80)
        
        # STEP 1: Initial optimization
        print("\n[STEP 1] User enters target_moyenne=69.78%")
        response = client.post('/cassiterite/optimize', data={
            'target_moyenne': '69.78',
            'target_total_quantity': '',
            'submit': 'Filter Stocks'
        }, follow_redirects=False)
        
        print(f"  Response status: {response.status_code}")
        
        # Check session after STEP 1
        with client.session_transaction() as sess:
            quantities_1 = sess.get('optimization_quantities', {})
            achieved_moyenne_1 = sess.get('optimization_achieved_moyenne', 0)
            achieved_qty_1 = sess.get('optimization_achieved_total_quantity', 0)
            mode_1 = sess.get('optimization_mode')
            
            print(f"  Session quantities: {len(quantities_1)} stocks selected")
            print(f"  Session achieved_moyenne: {achieved_moyenne_1:.4f}")
            print(f"  Session achieved_qty: {achieved_qty_1:.2f}")
            print(f"  Session mode: {mode_1}")
            print(f"  ✓ Step 1 OK" if mode_1 == 'result' and quantities_1 else "  ✗ Step 1 FAILED")
        
        # STEP 2: Re-edit GET request (user clicks "Re-Edit Selection")
        print("\n[STEP 2] User clicks 'Re-Edit Selection' (GET with mode=edit)")
        response = client.get('/cassiterite/optimize?mode=edit&target_moyenne=69.78', follow_redirects=False)
        print(f"  Response status: {response.status_code}")
        
        with client.session_transaction() as sess:
            quantities_2 = sess.get('optimization_quantities', {})
            achieved_moyenne_2 = sess.get('optimization_achieved_moyenne', 0)
            achieved_qty_2 = sess.get('optimization_achieved_total_quantity', 0)
            mode_2 = sess.get('optimization_mode')
            
            print(f"  Session quantities: {len(quantities_2)} stocks selected")
            print(f"  Session achieved_moyenne: {achieved_moyenne_2:.4f}")
            print(f"  Session achieved_qty: {achieved_qty_2:.2f}")
            
            # Check if re-edit preserved quantities from STEP 1
            if quantities_1 == quantities_2 and mode_2 == 'edit':
                print(f"  ✓ Step 2 OK - Quantities preserved!")
            else:
                print(f"  ✗ Step 2 FAILED - Quantities not preserved!")
                print(f"    quantities_1: {quantities_1}")
                print(f"    quantities_2: {quantities_2}")
        
        # STEP 3: Recalculate with potential edits
        print("\n[STEP 3] User adjusts quantities and clicks 'Recalculate'")
        
        # Build form data with adjustments (e.g., reduce first stock by 10%)
        form_data = {'action': 'recalculate'}
        with client.session_transaction() as sess:
            quantities = sess.get('optimization_quantities', {})
            for stock_id, qty in list(quantities.items())[:1]:  # Adjust first stock only
                form_data[f'qty_{stock_id}'] = str(qty * 0.9)  # 90% of original
        
        response = client.post('/cassiterite/optimize', data=form_data, follow_redirects=False)
        print(f"  Response status: {response.status_code}")
        
        with client.session_transaction() as sess:
            quantities_3 = sess.get('optimization_quantities', {})
            achieved_moyenne_3 = sess.get('optimization_achieved_moyenne', 0)
            achieved_qty_3 = sess.get('optimization_achieved_total_quantity', 0)
            mode_3 = sess.get('optimization_mode')
            
            print(f"  Session quantities: {len(quantities_3)} stocks selected")
            print(f"  Session achieved_moyenne: {achieved_moyenne_3:.4f}")
            print(f"  Session achieved_qty: {achieved_qty_3:.2f}")
            print(f"  Session mode: {mode_3}")
            print(f"  ✓ Step 3 OK" if mode_3 == 'result' and quantities_3 else "  ✗ Step 3 FAILED")
        
        # STEP 4: Re-edit again with new quantities
        print("\n[STEP 4] User clicks 'Re-Edit Selection' again (GET with mode=edit)")
        response = client.get('/cassiterite/optimize?mode=edit&target_moyenne=69.78', follow_redirects=False)
        print(f"  Response status: {response.status_code}")
        
        with client.session_transaction() as sess:
            quantities_4 = sess.get('optimization_quantities', {})
            mode_4 = sess.get('optimization_mode')
            
            # Check if STEP 4 re-edit preserved quantities from STEP 3
            if quantities_3 == quantities_4 and mode_4 == 'edit':
                print(f"  ✓ Step 4 OK - NEW quantities preserved!")
            else:
                print(f"  ✗ Step 4 FAILED - NEW quantities not preserved!")
        
        print("\n" + "="*80)
        print("WORKFLOW VALIDATION SUMMARY")
        print("="*80)
        print("✓ All steps should show quantities in session")
        print("✓ Re-edit should restore quantities from session")
        print("✓ Recalculate should update quantities in session")
        print("✓ Second re-edit should show NEW quantities, not original")
        print("\n")

if __name__ == '__main__':
    test_session_workflow()
