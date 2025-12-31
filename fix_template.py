#!/usr/bin/env python3
"""Fix the finance_deep_dive.html template to add dynamic tax calculations."""

import re

# Read the template file
with open('app/templates/grants/finance_deep_dive.html', 'r') as f:
    content = f.read()

# Fix 1: Add data attributes to the <tr> tag
old_tr = '''                        <tr class="vest-event-row {% if not ve_data.has_vested %}data-unvested{% endif %}"
                            data-shares-held="{{ ve_data.shares_held }}"
                            data-unrealized-gain="{{ ve_data.unrealized_gain }}"
                            data-is-long-term="{{ 'true' if ve_data.is_long_term else 'false' }}"
                            data-has-vested="{{ 'true' if ve_data.has_vested else 'false' }}">'''

new_tr = '''                        <tr class="vest-event-row {% if not ve_data.has_vested %}data-unvested{% endif %}"
                            data-shares-held="{{ ve_data.shares_held }}"
                            data-unrealized-gain="{{ ve_data.unrealized_gain }}"
                            data-is-long-term="{{ 'true' if ve_data.is_long_term else 'false' }}"
                            data-has-vested="{{ 'true' if ve_data.has_vested else 'false' }}"
                            data-tax-is-estimated="{{ 'true' if ve_data.tax_is_estimated else 'false' }}"
                            data-shares-vested="{{ ve.shares_vested }}"
                            data-current-price="{{ latest_stock_price }}">'''

if old_tr in content:
    content = content.replace(old_tr, new_tr)
    print("✓ Fixed <tr> data attributes")
else:
    print("✗ Could not find <tr> to fix")

# Fix 2: Add class to tax-paid cell
old_tax_cell = '''                            <td data-column="tax-paid">
                                {% if ve_data.tax_is_estimated %}
                                    <span style="color: var(--warning-color);" title="Estimated tax based on current stock price">
                                        ${{ "{:,.2f}".format(ve_data.tax_amount) }}*
                                    </span>'''

new_tax_cell = '''                            <td data-column="tax-paid" class="event-tax-paid">
                                {% if ve_data.tax_is_estimated %}
                                    <span class="estimated-tax" style="color: var(--warning-color);" title="Estimated tax - updates with tax sliders">
                                        ${{ "{:,.2f}".format(ve_data.tax_amount) }}*
                                    </span>'''

if old_tax_cell in content:
    content = content.replace(old_tax_cell, new_tax_cell)
    print("✓ Fixed tax-paid cell class and span")
else:
    print("✗ Could not find tax-paid cell to fix")

# Fix 3: Update JavaScript to calculate estimated taxes
old_js_loop = '''            // Calculate tax based on holding period
            // Short-term: taxed as ordinary income (federal + state)
            // Long-term: taxed at capital gains rate + state
            let eventTax;
            if (isLongTerm) {
                eventTax = eventUnrealizedGain * (capitalGainsRate + stateRate);
            } else {
                eventTax = eventUnrealizedGain * (federalRate + stateRate);
            }
            
            // Update the event tax display
            const eventTaxCell = row.querySelector('.event-tax-estimate');
            if (eventTaxCell) {
                eventTaxCell.textContent = '$' + eventTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            }
            
            grantTax += eventTax;'''

new_js_loop = '''            // Calculate tax based on holding period
            // Short-term: taxed as ordinary income (federal + state)
            // Long-term: taxed at capital gains rate + state
            let eventTax;
            if (isLongTerm) {
                eventTax = eventUnrealizedGain * (capitalGainsRate + stateRate);
            } else {
                eventTax = eventUnrealizedGain * (federalRate + stateRate);
            }
            
            // Update the event tax display (Est. Tax on Sale)
            const eventTaxCell = row.querySelector('.event-tax-estimate');
            if (eventTaxCell) {
                eventTaxCell.textContent = '$' + eventTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            }
            
            // Update "Tax Paid at Vest" for estimated taxes
            const taxIsEstimated = row.dataset.taxIsEstimated === 'true';
            if (taxIsEstimated) {
                const sharesVested = parseFloat(row.dataset.sharesVested);
                const currentPrice = parseFloat(row.dataset.currentPrice);
                
                // Calculate estimated vest tax using ordinary income rates (federal + state + FICA 7.65%)
                const ficaRate = 0.0765;
                const estimatedVestValue = sharesVested * currentPrice;
                const estimatedVestTax = estimatedVestValue * (federalRate + stateRate + ficaRate);
                
                // Update the "Tax Paid at Vest" cell
                const taxPaidSpan = row.querySelector('.event-tax-paid .estimated-tax');
                if (taxPaidSpan) {
                    taxPaidSpan.textContent = '$' + estimatedVestTax.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '*';
                }
            }
            
            grantTax += eventTax;'''

if old_js_loop in content:
    content = content.replace(old_js_loop, new_js_loop)
    print("✓ Fixed JavaScript calculation logic")
else:
    print("✗ Could not find JavaScript to fix")

# Fix 4: Update the tax info note
old_note = '''            <strong>📊 Tax Estimate Note:</strong> Values marked with an asterisk (*) are <em>estimated</em> taxes for future vesting events, 
            calculated at ~39% (22% federal + 9.3% state + 7.65% FICA). Actual withholding may vary based on your total income and tax situation.'''

new_note = '''            <strong>📊 Tax Estimate Note:</strong> Values marked with an asterisk (*) are <em>estimated</em> taxes for future vesting events. 
            These estimates update dynamically as you adjust the tax rate sliders above, using your selected federal and state rates plus FICA (7.65%). 
            Actual withholding may vary based on your total income and tax situation.'''

if old_note in content:
    content = content.replace(old_note, new_note)
    print("✓ Fixed tax estimate note")
else:
    print("✗ Could not find tax note to fix")

# Write the updated content
with open('app/templates/grants/finance_deep_dive.html', 'w') as f:
    f.write(content)

print("\n✅ Template file updated successfully!")
