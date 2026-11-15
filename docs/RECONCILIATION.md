# Consumption reconciliation

This check asks whether supplies reached patients, not merely the facility (brief §6.2). It runs on the server (`/api/reconciliation`) and is shown under **Resource transfer → Consumption check**.

## Inputs

- **Confirmed deliveries:** the quantity confirmed at the final scan point of each delivered shipment for the recipient's site, minus damaged and expired items.
- **Facility service data:** monthly figures per recipient site, entered by HQ admins through `POST /api/service-data` (no UI form yet). The metrics are:
  - `sam_admissions`
  - `cholera_cases_treated`
  - `vaccinations_given`
  - `stockout_days_<item_code>`, for example `stockout_days_3`.

## Rules

Settings are in `server/aidtrace_server/server.yaml` → `reconciliation`.

| Rule | Logic | Default |
|---|---|---|
| Deliveries exceed plausible use | Over the last `window_months`, confirmed delivered quantity > `excess_multiple` × (service metric × ratio) | 3 months, 1.5× |
| Stock-out despite recent delivery | Any stock-out days reported for an item while the site had a confirmed delivery in the last 30 days | n/a |

## Consumption ratios (PROVISIONAL, confirm with programme staff)

| Item (code) | Metric | Units per metric unit | Note |
|---|---|---|---|
| SAM medical supplies (3) | `sam_admissions` | 1.0 | 1 supply unit (for example an RUTF carton) per admission |
| Cholera kit (2) | `cholera_cases_treated` | 0.01 | 1 kit per 100 cases |
| Vaccines (5) | `vaccinations_given` | 1.0 | 1 dose per vaccination. Adjust for vial size and wastage. |

These are placeholders, not programme standards. Real ratios depend on kit composition, treatment protocols and vial sizes. A flag is a prompt for follow-up, for example through the independent verification queue. It is not evidence of diversion.

## Limitations

- No facility service data has been supplied (HeRAMS or programme reporting), so the check is empty until figures are entered.
- Facilities may legitimately stock up ahead of flooding or lean seasons (pre-positioning). Read flags alongside the shipment's `prepositioned` low-profile flag.
