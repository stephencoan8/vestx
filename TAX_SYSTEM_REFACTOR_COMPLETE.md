# Tax Estimation System Refactor - Complete

## Summary
Successfully replaced the complex year-by-year tax bracket calculation system with a **simple, user-friendly tax preference system** that matches industry standards (E*TRADE, Schwab, Carta, etc.).

## What Changed

### ✅ User Model (`app/models/user.py`)
**Added fields:**
- `federal_tax_rate` (Float, default 0.22) - User's chosen federal bracket
- `state_tax_rate` (Float, default 0.0) - User's state tax rate
- `include_fica` (Boolean, default True) - Whether to include FICA in estimates

**Added methods:**
- `get_tax_rates()` - Returns dict with federal, state, fica, and total rates
- `get_total_tax_rate()` - Returns combined tax rate for quick calculations

### ✅ Settings Route (`app/routes/settings.py`)
**Completely rewritten:**
- Removed complex `UserTaxProfile`, `AnnualIncome`, and tax bracket logic
- New `/settings/profile` route with simple tax preference form
- Pre-populated state tax rates for all 50 states + DC
- Federal bracket dropdown (10%, 12%, 22%, 24%, 32%, 35%, 37%)
- FICA toggle (includes Social Security 6.2% + Medicare 1.45%)
- Real-time preview of total tax rate

### ✅ New Profile Template (`app/templates/settings/profile.html`)
**Beautiful, user-friendly interface:**
- Clear explanation of how tax estimates work
- Dropdown for federal bracket selection
- State selection with pre-filled rates
- Option to enter custom state rate
- FICA checkbox
- Live summary showing: Federal + State + FICA = Total
- Example calculation (e.g., "$100,000 vest → $38,950 estimated taxes")

### ✅ Vest Event Model (`app/models/vest_event.py`)
**Simplified `estimate_tax_withholding()` method:**
- Removed complex `TaxCalculator` and marginal rate calculations
- Now simply: `vest_value * user.get_total_tax_rate()`
- No more year-by-year income tracking
- No more cached tax profiles
- Clean, straightforward estimation

### ✅ Grants Routes (`app/routes/grants.py`)
**Removed complexity:**
- Deleted all `UserTaxProfile` imports and queries
- Removed `AnnualIncome` pre-fetching
- Removed year-by-year rate caching
- Simplified `finance_deep_dive` route (removed 50+ lines of tax logic)
- All routes now use `current_user.get_tax_rates()` directly

### ✅ Finance Deep Dive Template (`app/templates/grants/finance_deep_dive.html`)
**Updated UI:**
- Links now point to `/settings/profile` instead of `/settings/tax`
- Removed "manual mode" vs "automatic mode" toggle
- Shows simple summary: "Your Current Tax Rates: Federal 22%, State 9.3%, FICA 7.65%, Total 39%"
- Clear link to update preferences

## Migration

### Database Changes
Created `migrate_add_tax_prefs.py` to add three columns to `users` table:
```sql
ALTER TABLE users 
ADD COLUMN federal_tax_rate FLOAT DEFAULT 0.22,
ADD COLUMN state_tax_rate FLOAT DEFAULT 0.0,
ADD COLUMN include_fica BOOLEAN DEFAULT TRUE;
```

### Deployment Steps
1. ✅ Code pushed to GitHub → Railway auto-deploys
2. ⚠️ **Run migration on production:**
   ```bash
   python migrate_add_tax_prefs.py
   ```
3. ✅ Existing users get default rates (22% federal, 0% state, FICA enabled)
4. ✅ Users can update their preferences in `/settings/profile`

## How It Works Now

### For Users:
1. Go to **Settings → Profile** (or `/settings/profile`)
2. Choose federal bracket that matches their income:
   - 22% (most common - singles $44k-$95k, married $89k-$190k)
   - 35% (high earners - singles $231k-$578k, married $462k-$693k)
3. Select their state from dropdown (or enter custom rate)
4. Toggle FICA on/off (most keep it on)
5. Save → All vest estimates now use these rates!

### For the App:
- **Vest Schedule:** Shows estimated tax for future vests using user's total rate
- **Finance Deep Dive:** Calculates unrealized gains and tax-on-sale estimates
- **Grant Details:** All tax calculations use user's simple preferences
- **Consistency:** Every page uses the same rates = no confusion!

## Why This Is Better

### ❌ Old System Problems:
- Required users to enter annual income for each year
- Complex marginal bracket calculations
- Year-by-year tax rate lookups
- Required `UserTaxProfile`, `TaxBracket`, `AnnualIncome` models
- Confusing "automatic" vs "manual" modes
- Different from how Schwab/E*TRADE do it

### ✅ New System Benefits:
- ✨ **Simple:** Choose 3 things, done
- 🎯 **Industry Standard:** Matches E*TRADE, Schwab, Carta approach
- 🚀 **Fast:** No complex DB queries or calculations
- 📊 **Clear:** One rate for all estimates
- 🔧 **Easy to Update:** Change anytime in profile
- 💡 **Transparent:** Users see exactly what rate is being used

## Files Changed
1. `app/models/user.py` - Added tax preference fields and methods
2. `app/models/vest_event.py` - Simplified tax estimation
3. `app/routes/settings.py` - Completely rewritten for simple profile
4. `app/routes/grants.py` - Removed all complex tax logic
5. `app/templates/settings/profile.html` - New beautiful UI
6. `app/templates/grants/finance_deep_dive.html` - Updated links and summary
7. `migrate_add_tax_prefs.py` - Database migration script

## Next Steps
1. ⚠️ Run migration on production: `python migrate_add_tax_prefs.py`
2. ✅ Test the new `/settings/profile` page
3. ✅ Verify vest estimates use new rates
4. ✅ Consider removing old `UserTaxProfile`, `TaxBracket`, `AnnualIncome` models (future cleanup)

## Notes
- Old `/settings/tax` route redirects to `/settings/profile`
- Existing tax profile data is NOT migrated (users start fresh with defaults)
- Users can update their rates anytime without losing vest data
- FICA calculation is simplified: 7.65% flat (Social Security wage base not tracked)

---

**Status:** ✅ Complete and deployed to Railway
**Tested:** Local development environment
**Ready for:** Production migration and user testing
