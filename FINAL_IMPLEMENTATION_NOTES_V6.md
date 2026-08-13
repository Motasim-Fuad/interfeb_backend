# Interfab QC - Final Implementation Notes V6

## Final production flow
Supplier QC Profile -> Fabric Receive / Consignment QC Setup -> Finalize / version -> PLM snapshot -> Color -> Roll planning -> Factory App actual inspection -> Backend calculations -> Dashboard/App report + canonical PDF print.

## Source-report alignment
The implementation was re-checked against the supplied Interfab/J.Crew QC report set (`WX0651 (1).pdf`).

- 4-Point Fabric Inspection: dynamic defects/ranges, dashboard planning values, actual inspection values from App, backend total/%/points-per-yard calculations.
- GSM: Required GSM + +/- tolerance are supplier/profile defaults; Over/Less = (Actual - Required) / Required * 100; overall Before/After status is based on average deviation vs tolerance. Empty After Wash data is not reported as PASS.
- Moisture: supplier profile stores composition-specific maximum moisture index/acceptance rows. Fabric Receive selects/resolves the applicable composition standard; App only records actual reading and wash stage; backend calculates PASS/FAIL.
- Skew & Bowing: supplier profile stores width-specific Solid/Stripe acceptance amounts in supplier measurement unit. Fabric Receive resolves width + Solid/Stripe + applicable acceptance. App records AC/BD; backend calculates skew amount and percentage and compares the amount to the applicable acceptance amount.
- Relaxation: supplier profile stores relaxation duration only. No supplier acceptance threshold. App records before/after measurements and times; backend calculates length/width change percentages.
- Shrinkage: no supplier acceptance field. App records before/after readings; backend calculates result. Buyer/PLM technical requirements can be represented separately when available rather than invented as a supplier default.
- Shade Check: supplier-specific fixed point presets are configured in Supplier QC Profile, copied into Consignment snapshot, then read by App. Length-wise and width-wise points remain historical even if supplier defaults later change.
- Color Continuity: App records actual before/after shade; fixed context comes from PLM/roll planning.
- Fabric Correlation: no tolerance field and no App data-entry form. Supplier Mill Report values come from dashboard/planning; Factory values derive from 4-Point inspection; average point and status are backend-derived. App is view/table/print only.
- Factory Report: no App data-entry form. Report derives from PLM/roll planning + 4-Point inspection; standard comes from the finalized 4-Point overall/average acceptance snapshot. App is view/table/print only.

## Dashboard
- `Supplier QC Profiles` is the single supplier configuration area for GSM, Moisture standards, Skew/Width standards, Relaxation hours, 4-Point defaults, Shade points, and Correlation Mill Report defaults.
- Old separate Acceptance/Composition/Width/Shade configuration menu entries are removed.
- Defect Types and Defect Ranges stay separate dynamic master data.
- Supplier owns measurement unit (INCH/CM).
- Fabric Receive auto-loads supplier profile defaults, resolves applicable Moisture/Skew setup, allows consignment review/override, then finalizes/version-locks.
- PLM snapshots the finalized consignment QC config and unit.
- Roll planning supports marked width, marked length, planned quantity, supplier/mill total defect point, and bulk roll creation.

## Factory App
- No PLM creation.
- No user-facing UUIDs; business PLM code is shown.
- Dashboard/planning values are read-only and are not re-entered in the App.
- Relaxation time is read-only from Dashboard.
- Correlation and Factory Report are derived/read-only.
- Immediate roll-level persistence; no redundant whole-report Save Report pattern.
- Dynamic defects/ranges and supplier-specific Shade snapshots.
- Responsive implementation uses MediaQuery/LayoutBuilder; no ScreenUtil dependency.

## Printing
App and Dashboard both request the same backend PDF endpoint (`/reports/pdf/{module}/{plm_id}`), so calculations and document layout do not diverge. Wide reports use landscape pages and bounded tables to avoid clipping.

## Docker / fresh DB
`docker-compose.yml` loads `.env.example` and treats `.env` as an optional local override, so a fresh unzip can start without the earlier `.env not found` failure. Use `.env` for production/local secret overrides.
