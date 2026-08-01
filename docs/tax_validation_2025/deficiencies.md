# VestX tax engine deficiencies (validation study 2025)

Updated after std-ded + preferential-AMT fixes (2026-07-31).

Scope: decision-grade issues only (not ~0.5% noise).

## Fixed (this change set)

- **Sale path standard deduction:** `_federal_state_layer` now applies federal (and CA when state=CA) standard deductions on gross inputs. Sale incremental tax matches full-year CG delta (Case 10: $107,485 = $107,485).
- **AMT regular tax uses post-std-ded tax:** Form 6251-style comparison base is correct; classic ISO AMT due matches independent ref (Case 12: $40,071; Case 13: $228,689).
- **Phantom AMT on pure LTCG:** Federal TMT now taxes the LTCG slice at preferential 0/15/20% rates (not 26/28%). Large LT + modest wages no longer invents ~$35k fake AMT.
- **Vest path simplified:** passes gross ordinary into the shared layer (no double std-ded bookkeeping).

## Remaining (not calculation bugs — UX / tables / process)

- Public free tools do not model CA Schedule P AMT or multi-year federal AMT credit handoff; VestX includes planning CA AMT — totals will exceed Carta/ESO 'federal AMT due only' and must be read as federal+CA. Risk: users comparing VestX all-in incremental to federal-only AMT calculators think VestX is 'too high' without understanding CA layer.
- 2025 federal standard deduction in VestX is $15,000 single; some 2025 IRS updates / OBBBA communications cite higher amounts (~$15,750). Material only on lower wages; document as table lag.
- ESO Fund public docs mix 2025 vs 2026 exemption figures ($88,100 vs $90,100 in FAQ); VestX uses internal FED_AMT_EXEMPTION tables. Cross-site AMT $ differences of a few thousand can be table-year mismatch, not logic bugs — still verify before large exercises.
- Grants Finance still shows per-lot 'if sold alone' tax that can understate portfolio tax vs stacked multi-lot total (now engine-based, but two numbers can confuse decisions).
- No automated regression suite ties SmartAsset/Carta outputs into CI; confidence depends on periodic hybrid studies like this one (plus unit regressions for Cases 10/12).

## What is trustworthy

- Full-year W-2 federal progressive + CA PIT + FICA (SS base, Medicare) for single CA 2025.
- Preferential LTCG fill and STCG-as-ordinary on full-year and sale paths (shared layer).
- Sale incremental ≈ full-year (wages+gains − wages) for pure CG stacks.
- ISO exercise-and-hold federal AMT due matches independent Form 6251-style math after std ded.
- Cashless DD is not priced as pure 15% LTCG.

## Public tool limits (not VestX defects)

- SmartAsset: strong on wages/FICA/state; weak on ISO AMT preference and multi-year credit.
- Carta/ESO AMT: federal AMT focus; not CA AMT; not full FICA stack; year tables may lag.
