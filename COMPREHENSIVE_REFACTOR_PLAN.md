# COMPREHENSIVE REFACTORING PLAN

## PROBLEMS IDENTIFIED:

### 1. Price Retrieval Broken
- `vest_event.share_price_at_vest` property calls `get_latest_user_price()`
- `get_latest_user_price()` requires `current_user` to be authenticated
- This fails when called from property methods in template context
- Result: Prices show as $0

### 2. Wrong UI Labels
- "Strike Price" shown for ALL grant types
- Should only show for ISOs
- RSUs should show "Grant Price" or "Cost Basis"

### 3. Duplicate Calculations
- Cost basis calculated in:
  * finance_deep_dive route (lines 569-615)
  * get_estimated_sale_tax() method
  * Templates (Jinja2 expressions)
- Tax estimates calculated in:
  * get_comprehensive_tax_breakdown()
  * get_estimated_sale_tax()
  * finance_deep_dive route
  * vest_detail route
- Values calculated in multiple properties and routes

### 4. No Single Source of Truth
- Each page recalculates same data differently
- Routes duplicate logic
- Templates have calculations
- Properties depend on request context

### 5. Properties with Side Effects
- `share_price_at_vest` makes database queries
- `value_at_vest` calls `share_price_at_vest` (nested queries)
- `net_value` calls both above (even more queries)
- Creates N+1 query problem
- Breaks outside web request context

## SOLUTION ARCHITECTURE:

### Core Principle: **Backend Calculations, Frontend Display**

### 1. Centralized Data Method
**VestEvent.get_complete_data(user_key, current_price, tax_profile, annual_incomes, sales_data, exercises_data)**
- Returns comprehensive dict with ALL vest data
- One method call = all calculations done
- No properties with database queries
- Works outside request context
- Single source of truth

### 2. Explicit Parameter Passing
- No hidden dependencies on `current_user`
- Methods take explicit parameters
- Can be called from anywhere (routes, background tasks, tests)
- Better testability

### 3. Route Simplification
Routes should ONLY:
1. Get user data (vest, grants, tax profile)
2. Get user's decryption key  
3. Call `get_complete_data()`
4. Pass dict to template
5. Handle POST requests (updates)

Routes should NOT:
- Calculate anything
- Duplicate logic
- Have business logic

### 4. Template Simplification
Templates should ONLY:
- Display data from vest_data dict
- Format numbers/dates
- Show/hide sections based on flags

Templates should NOT:
- Calculate values
- Call properties
- Have business logic
- Make decisions about grant types

## IMPLEMENTATION PLAN:

### Phase 1: Core Infrastructure ✅
- [x] Create VestEvent.get_complete_data() method
- [x] Add user_key parameter support
- [x] Return comprehensive dict with all data
- [x] Document the refactoring plan

### Phase 2: Route Refactoring
- [ ] Update vest_detail route to use get_complete_data()
- [ ] Update finance_deep_dive route
- [ ] Remove duplicate calculations from routes
- [ ] Simplify error handling

### Phase 3: Template Refactoring
- [ ] Update vest_detail.html to use vest_data dict
- [ ] Fix labels: "Strike Price" only for ISOs
- [ ] Remove all calculations from templates
- [ ] Remove property calls

### Phase 4: Property Cleanup
- [ ] Remove properties that make DB queries
- [ ] Keep simple properties (has_vested, etc.)
- [ ] Add explicit methods where needed

### Phase 5: Consistency Check
- [ ] Ensure all pages use centralized methods
- [ ] Check finance_deep_dive
- [ ] Check sale_planning
- [ ] Check list/view pages

### Phase 6: Testing
- [ ] Test vested vests
- [ ] Test unvested vests
- [ ] Test ISOs vs RSUs
- [ ] Test with/without tax profile
- [ ] Test sales and exercises

## FILES TO MODIFY:

1. app/models/vest_event.py ✅
   - Added get_complete_data() method
   
2. app/routes/grants.py
   - Refactor vest_detail route
   - Refactor finance_deep_dive route
   
3. app/templates/grants/vest_detail.html
   - Use vest_data dict
   - Fix labels
   - Remove calculations
   
4. app/templates/grants/finance_deep_dive.html
   - Use centralized data
   - Remove calculations

## BENEFITS:

1. **Correctness**: Prices will actually show (no more $0)
2. **Consistency**: Same calculations everywhere
3. **Performance**: No N+1 queries, single calculation
4. **Maintainability**: Update logic in one place
5. **Testability**: Methods can be tested in isolation
6. **Clarity**: Clear separation of concerns
7. **Reliability**: No hidden dependencies on request context

## NEXT STEPS:

1. Test get_complete_data() method
2. Update vest_detail route (see REFACTOR_vest_detail_route.py)
3. Update template (see REFACTOR_template_guide.md)
4. Test end-to-end
5. Apply same pattern to finance_deep_dive
6. Clean up old code
