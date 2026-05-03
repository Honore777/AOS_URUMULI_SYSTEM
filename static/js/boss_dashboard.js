// static/js/boss_dashboard.js
// AJAX-driven dashboard updater for boss dashboard (no pagination)

function readFilters() {
    return {
        mineral: document.getElementById('filter-mineral')?.value || '',
        from: document.getElementById('filter-from')?.value || '',
        to: document.getElementById('filter-to')?.value || ''
    };
}

async function fetchDashboardData(params) {
    const qs = new URLSearchParams(params);
    const res = await fetch(`/boss/dashboard/data?${qs.toString()}`, {
        headers: { 'Accept': 'application/json' }
    });
    if (!res.ok) throw new Error('Server error: ' + res.status);
    return res.json();
}

function formatAmount(v) {
    return (v == null) ? '0.00' : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatNumberWithDecimals(v, decimals = 2) {
    if (v == null || isNaN(v)) return Number(0).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return Number(v).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

async function fetchRemainingStock() {
    try {
        const res = await fetch('/api/remaining_stock', {
            headers: { 'Accept': 'application/json' }
        });
        if (!res.ok) throw new Error('Failed to fetch remaining stock');
        const data = await res.json();
        const el = document.getElementById('kpi-total-remaining-kg');
        if (el) {
            el.textContent = formatNumberWithDecimals(data.total_remaining_kg || 0, 2);
        }
    } catch (error) {
        console.error('Error fetching remaining stock:', error);
    }
}

function updateKPIs(kpis) {
    if (!kpis) return;
    const mapping = {
        'total_gross_profit': 'kpi-total-gross-profit',
        'total_net_profit': 'kpi-total-net-profit',
        'total_inventory_value': 'kpi-total-inventory-value',
        'total_cost_of_stock_sold': 'kpi-total-cogs',
        'total_supplier_debt': 'kpi-total-supplier-debt',
        'total_customer_debt': 'kpi-total-customer-debt',
        'total_internal_worker_payments': 'kpi-total-internal-worker-payments',
        'total_internal_expenses': 'kpi-total-internal-worker-payments',
        'total_cash_at_hand': 'kpi-total-cash-at-hand'
    };
    Object.entries(mapping).forEach(([key, elid]) => {
        const el = document.getElementById(elid);
        if (el && (key in kpis)) el.textContent = formatAmount(kpis[key]);
    });
}

function updatePerMineralKPIs(copper, cass) {
    if (copper) {
        const map = {
            'total_sales': 'copper-total-sales',
            // Inventory Value (Coltan)
            'inventory_value': 'copper-inventory-value',
            'cogs': 'copper-cogs',
            'gross_profit': 'copper-gross-profit',
            'supplier_debt': 'copper-supplier-debt',
            'customer_debt': 'copper-customer-debt',
            'cash_position': 'copper-cash-position'
        };
        Object.entries(map).forEach(([k, id]) => {
            const el = document.getElementById(id);
            if (el && (k in copper)) el.textContent = formatAmount(copper[k]);
        });
    }
    if (cass) {
        const map = {
            'total_sales': 'cass-total-sales',
            // Inventory Value (Cassiterite)
                'inventory_value': 'cass-inventory-value',
                'cogs': 'cass-cogs',
                'gross_profit': 'cass-gross-profit',
            'supplier_debt': 'cass-supplier-debt',
            'customer_debt': 'cass-customer-debt',
            'cash_position': 'cass-cash-position'
        };
        Object.entries(map).forEach(([k, id]) => {
            const el = document.getElementById(id);
            if (el && (k in cass)) el.textContent = formatAmount(cass[k]);
        });
    }
}

function renderRecentPlansTable(plans) {
    const tbody = document.getElementById('recent-plans-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!plans || plans.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4">No results</td></tr>';
        return;
    }
    for (const p of plans) {
        const tr = document.createElement('tr');
        tr.className = 'hover:bg-gray-50 text-sm';
        tr.innerHTML = `
      <td class="px-4 py-3 text-gray-700">${p.created_at || 'N/A'}</td>
      <td class="px-4 py-3 text-gray-700 uppercase">${p.mineral_type || ''}</td>
      <td class="px-4 py-3 text-gray-700">${p.customer || 'N/A'}</td>
      <td class="px-4 py-3 text-gray-700">${p.batch_id || 'N/A'}</td>
      <td class="px-4 py-3 text-gray-700">${p.status || ''}</td>
      <td class="px-4 py-3 text-right text-gray-700">${p.total_kg != null ? Number(p.total_kg).toFixed(2) : 'N/A'}</td>
    `;
        tbody.appendChild(tr);
    }
}

async function loadAndRender(params) {
    try {
        const data = await fetchDashboardData(params);
        updateKPIs(data.kpis);
        // update per-mineral cards as well
        updatePerMineralKPIs(data.copper, data.cassiterite);
        renderRecentPlansTable(data.recent_plans);
        // future: update pending_reviews and recent_reviews
    } catch (err) {
        console.error(err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const applyBtn = document.getElementById('filter-apply');
    const initialFilters = readFilters();
    loadAndRender(initialFilters);
        fetchRemainingStock();
    applyBtn?.addEventListener('click', (e) => {
        e.preventDefault();
        const params = readFilters();
        loadAndRender(params);
        fetchRemainingStock();
    });
    // Reset button clears filters and reloads default data
    const resetBtn = document.getElementById('filter-reset');
    function clearFilters() {
        const mineralEl = document.getElementById('filter-mineral');
        const fromEl = document.getElementById('filter-from');
        const toEl = document.getElementById('filter-to');
        if (mineralEl) mineralEl.value = '';
        if (fromEl) fromEl.value = '';
        if (toEl) toEl.value = '';
        // reload default dataset
        loadAndRender(readFilters());
    }
    resetBtn?.addEventListener('click', (e) => {
        e.preventDefault();
        clearFilters();
    });
});
