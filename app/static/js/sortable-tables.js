/**
 * Sortable Tables
 * Makes table columns sortable by clicking on headers
 */

document.addEventListener('DOMContentLoaded', function() {
    // Find all sortable tables
    const tables = document.querySelectorAll('.sortable-table');
    
    tables.forEach(table => {
        const headers = table.querySelectorAll('th[data-sort]');
        
        headers.forEach((header) => {
            header.style.cursor = 'pointer';
            header.style.userSelect = 'none';
            
            // Add sort indicator
            const indicator = document.createElement('span');
            indicator.className = 'sort-indicator';
            indicator.innerHTML = ' ↕';
            header.appendChild(indicator);
            
            header.addEventListener('click', () => {
                // Use the header's real column index — a leading checkbox
                // (or any th without data-sort) would otherwise shift every
                // sort by one, so "date" sorted basis $ instead of dates.
                sortTable(table, header.cellIndex, header);
            });
        });
    });
});

function parseSortDate(value) {
    if (value == null) return null;
    const s = String(value).trim();
    if (!s || s === '—' || s === '-') return null;
    const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) {
        return new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3])).getTime();
    }
    const t = Date.parse(s);
    return Number.isNaN(t) ? null : t;
}

function sortTable(table, columnIndex, header) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    // Skip empty-state / spacer rows
    const rows = Array.from(tbody.querySelectorAll('tr')).filter(function (tr) {
        return !tr.classList.contains('tax-lot-empty') && !tr.querySelector('.empty-state');
    });
    const emptyRows = Array.from(tbody.querySelectorAll('tr')).filter(function (tr) {
        return tr.classList.contains('tax-lot-empty') || tr.querySelector('.empty-state');
    });
    const sortType = header.getAttribute('data-sort');
    const currentOrder = header.getAttribute('data-order') || 'asc';
    const newOrder = currentOrder === 'asc' ? 'desc' : 'asc';
    
    // Remove sort indicators from all headers
    table.querySelectorAll('th[data-sort]').forEach(h => {
        h.removeAttribute('data-order');
        const indicator = h.querySelector('.sort-indicator');
        if (indicator) {
            indicator.innerHTML = ' ↕';
        }
    });
    
    // Set new sort order
    header.setAttribute('data-order', newOrder);
    const indicator = header.querySelector('.sort-indicator');
    if (indicator) {
        indicator.innerHTML = newOrder === 'asc' ? ' ▲' : ' ▼';
    }

    function cellValue(row) {
        const cell = row.cells[columnIndex];
        if (!cell) return '';
        // Qty column: prefer live input value
        const qtyInput = cell.querySelector('.lot-qty, input[type="number"]');
        if (qtyInput) return qtyInput.value || '0';
        if (cell.hasAttribute('data-value')) return cell.getAttribute('data-value') || '';
        return cell.textContent.trim();
    }
    
    // Sort rows
    rows.sort((a, b) => {
        let aValue = cellValue(a);
        let bValue = cellValue(b);
        
        let comparison = 0;
        
        switch(sortType) {
            case 'number':
                // Remove currency symbols and commas
                const aNum = parseFloat(String(aValue).replace(/[$,]/g, '')) || 0;
                const bNum = parseFloat(String(bValue).replace(/[$,]/g, '')) || 0;
                comparison = aNum - bNum;
                break;
                
            case 'date':
                // Empty dates sort last. Parse YYYY-MM-DD as local dates so
                // ISO lots / RSU vests / ESPP purchases don't shift a day.
                const aDate = parseSortDate(aValue);
                const bDate = parseSortDate(bValue);
                if (aDate == null && bDate == null) comparison = 0;
                else if (aDate == null) comparison = 1;
                else if (bDate == null) comparison = -1;
                else comparison = aDate - bDate;
                break;
                
            case 'text':
            default:
                comparison = String(aValue).localeCompare(String(bValue), undefined, { sensitivity: 'base' });
                break;
        }
        
        return newOrder === 'asc' ? comparison : -comparison;
    });
    
    // Re-append sorted rows (then empty rows)
    rows.forEach(row => tbody.appendChild(row));
    emptyRows.forEach(row => tbody.appendChild(row));
}
