/**
 * Sortable Tables
 * Makes table columns sortable by clicking on headers
 */

document.addEventListener('DOMContentLoaded', function() {
    // Find all sortable tables
    const tables = document.querySelectorAll('.sortable-table');
    
    tables.forEach(table => {
        const headers = table.querySelectorAll('th[data-sort]');
        
        headers.forEach((header, index) => {
            header.style.cursor = 'pointer';
            header.style.userSelect = 'none';
            
            // Add sort indicator
            const indicator = document.createElement('span');
            indicator.className = 'sort-indicator';
            indicator.innerHTML = ' ↕';
            header.appendChild(indicator);
            
            header.addEventListener('click', () => {
                sortTable(table, index, header);
            });
        });
    });
});

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
                // Empty dates sort last
                if (!aValue && !bValue) comparison = 0;
                else if (!aValue) comparison = 1;
                else if (!bValue) comparison = -1;
                else {
                    const aDate = new Date(aValue);
                    const bDate = new Date(bValue);
                    comparison = aDate - bDate;
                }
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
