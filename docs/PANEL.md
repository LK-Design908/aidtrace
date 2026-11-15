# Panel data dictionary (Somalia pilot)

File: `data/processed/panel.parquet` (identical to `panel_som.parquet`). Build with:

```powershell
& $env:USERPROFILE\.venvs\aidtrace\Scripts\python.exe -I -m aidtrace_pipeline.panel --country SOM [--force]
```

Each source is rebuilt only if its raw file, the boundaries, or pipeline code changed since the last build (`refresh()` in every loader). `--force` rebuilds everything. Coverage, join rates and notes for each source are written to `data/processed/build_report_som.json`.

## Shape (build of 2026-11-14)

- **74 units × 118 months** (2017-01 to 2026-10) = 8,732 rows, 64 columns.
- Units: the 91 COD-AB v03 districts, with Banadir's 18 polygons (17 named districts plus "Unspecified") merged into `SO22`. See `DECISIONS.md`. The crosswalk is `data/processed/spine/som_crosswalk.csv` and the geometries are `data/processed/spine/som_units.parquet`.
- End month = the last complete calendar month before the build date, capped at the latest month any observed source (UCDP, WFP, DTM) reaches.

## All six SRP countries (Milestone 8)

Files: `data/processed/panel_<iso3>.parquet` (`panel.parquet` remains the Somalia pilot copy). Every country has the same columns, so a source a country lacks appears as all-NaN with `<source>_missing = 1`.

| Country | Units (admin-2) | Months | Rows | UCDP | FEWS NET | WFP markets | DTM | ACLED (restricted) |
|---|---|---|---|---|---|---|---|---|
| Somalia | 74 (Banadir merged) | 2017-01..2026-10 | 8,732 | ✓ | CS 2024-06, 2026-10; projections | 40 | ✓ ETT 2026-06+ | ✓ |
| Ethiopia | 107 zones | 2017-01..2026-09 | 12,519 | ✓ | CS 2024-06; ML2 2024-10..2025-01 | 91 | ✗ | ✓ |
| Kenya | 290 sub-counties | 2017-01..2026-10 | 34,220 | ✓ | CS 2024-06; ML1/ML2 2024 and 2026 | 108 | ✗ (names-only survey not joined) | ✓ |
| South Sudan | 79 counties | 2017-01..2026-10 | 9,322 | ✓ (plus 713 pre-2011 "Sudan" events placed spatially) | CS 2026-06; projections | 38 | ✓ Event Tracking 2026-01..09 (97% p-code join) | ✓ |
| Sudan | 189 localities | 2017-01..2026-09 | 22,113 | ✓ | projections only (ML1/ML2 2026) | 19 | ✗ | ✓ |
| Djibouti | 20 | 2017-01..2026-09 | 2,340 | ✓ (61 events since 1989) | ✗ | 6 | ✗ | ✓ (0 events) |

UCDP coverage ends with the GED release (2025-12) for every country, even where a country's last event is earlier. After that point, unit-months are recorded zeros up to 2025-12 and NaN beyond.


### Static district attributes and COW (added 2026-11-15)

These are built by `loaders/static.py` into `data/processed/static/<iso>_static.parquet`, and are constant over months. Where a source doesn't cover a country, the value is NaN (never 0).

| Column | Countries | Description |
|---|---|---|
| `pop_total`, `pop_u1`, `pop_u5`, `pop_u15`, `pop_f15_49` | KEN, SSD | WorldPop 2020 age/sex population. The total is the sum of the 36 age/sex bands. Kenya 55.7 M (above the 2019 census, 47.6 M: WorldPop figures are modelled), South Sudan 11.1 M. |
| `pop_density_km2` | KEN, SSD | Population per km² |
| `bldg_count`, `bldg_area_m2`, `bldg_density_km2` | SOM | WorldPop building patterns v1.1. Somalia has 3.13 M buildings. |
| `bldg_urban_share` | SOM | Share of buildings in WorldPop "urban" cells (25% nationally) |
| `bldg_imagery_year` | SOM | Building-weighted mean year of the source imagery (about 2017) |
| `urban_extent_share`, `urban_points` | SOM | Share of district area inside WorldPop urban extents, and the number of named urban centres |
| `mid_incidents_1992_2010`, `mid_disputes_1992_2010` | all | COW MIDLOC 2.1 militarized interstate dispute incidents located in the district (historical context only) |
| `weight_source` | all | Basis for people-weighted IPC: `population` (KEN, SSD), `buildings` (SOM), `area` (DJI, SDN, ETH) |
| `pop_missing` | all | 1 where there is no population estimate |
| `nmc_milex`, `nmc_milper`, `nmc_tpop`, `nmc_upop`, `nmc_cinc`, `nmc_pec`, `nmc_irst` | all | COW National Material Capabilities v7 for the month's calendar year. NaN after 2022 (the last NMC year). |
| `ipc_cs_p3plus_pop`, `ipc_cs_max_pop`, `ipc_proj_p3plus_pop`, `ipc_proj_max_pop` | KEN, SSD, SOM | IPC indicators weighted by the country's weight grid instead of land area. They describe the share of people **living in areas classified** Phase 3+, which is not IPC's own population-in-phase estimate. |

The need index uses `ipc_*_p3plus_pop` where it exists, and scores displacement per 1,000 residents where population is known (`need.scaling.displacement_per_1000_full`, 20 per 1,000 = full score).

## Missing-data rules

- Nothing is silently filled with zero. Each source has a `<source>_missing` flag (1 = no observation for that unit-month). Need-index inputs also have per-column `<col>_missing` flags.
- **Event counts (UCDP):** inside the source's coverage (1989-01 to 2025-12), a unit-month without events is **0**, meaning nothing was *recorded*. Remote areas are under-reported, so treat 0 as a lower bound. Outside coverage (2026 onwards) the value is NaN.
- **Snapshots (FEWS NET, WFP, DTM, VIEWS):** observed only where the source has a value. Everything else is NaN.

| Source | Coverage in panel | Unit-months missing |
|---|---|---|
| UCDP GED v26.1 | 2017-01 to 2025-12 | 8.5% (the 10 months of 2026) |
| FEWS NET | 2024-06 (CS), 2024-10 to 2025-01 (ML2), 2026-10 (CS + ML1) | 94.9% |
| WFP prices | 2025-01 to 2026-08 (anomalies from 2025-07) | 92.1% |
| IOM DTM ETT | 2026-06 to 2026-10, assessed districts only | 99.5% |
| VIEWS | 2026-10 only (forecast) | 99.2% |

## Columns

### Keys and unit attributes
| Column | Type | Description |
|---|---|---|
| `unit_id` | str | Modelling-unit code: COD-AB `adm2_pcode`, or `SO22` for merged Banadir |
| `month` | datetime | First day of the calendar month |
| `unit_name`, `adm1_pcode`, `adm1_name`, `country` | str | Labels |
| `area_km2` | float | Unit area (Africa Albers Equal Area) |

### UCDP GED (open; core conflict source and model outcome)
Events with `where_prec` ≤ 3 (exact, within ~25 km, or admin-2 level) are placed by coordinates. Points up to 5 km offshore are snapped to the nearest unit. Coarser events are never forced into a district.

| Column | Description |
|---|---|
| `ucdp_events` | Events placed in the unit whose start and end dates fall in this month |
| `ucdp_deaths_best` / `_low` / `_high` | Sum of UCDP fatality estimates for those events |
| `ucdp_deaths_civilians` | Civilian deaths |
| `ucdp_events_fatal` | Events with `best` > 0 |
| `ucdp_fatal_any` | **Model outcome:** 1 if ≥ 1 fatal event in the unit-month |
| `ucdp_events_{statebased,nonstate,onesided}`, `ucdp_deaths_{...}` | By `type_of_violence` (1/2/3) |
| `ucdp_events_multimonth` | Events whose dates span months, counted in the start month and kept out of the core counts |
| `ucdp_imprecise_events_adm1`, `ucdp_imprecise_deaths_adm1` | Events located only to admin-1 (`where_prec` 4) in this unit's region, repeated on **every** unit of the region. Context, not location. |
| `ucdp_imprecise_events_country` | Events located to country level or along a linear feature (`where_prec` 5–7), repeated on every unit |
| `ucdp_missing` | 1 outside GED coverage |

### FEWS NET (IPC 3.1-compatible), area-weighted overlay
FEWS NET polygons are dissolved by phase. Values are therefore area-weighted, which over-weights sparsely populated land. Switch to population weighting once WorldPop is added.

| Column | Description |
|---|---|
| `ipc_cs_max` | Highest current phase covering ≥ 5% of the unit's area |
| `ipc_cs_mean` | Area-weighted mean current phase |
| `ipc_cs_p3plus` | Share of the unit's area in Phase 3 or above (current) |
| `ipc_cs_coverage` | Share of the unit's area that is classified |
| `ipc_cs_idp_max` | Highest phase among IDP-settlement features in the unit |
| `ipc_cs_observed` | 1 if a current classification exists for this month |
| `ipc_proj_{max,mean,p3plus,coverage,idp_max}` | Same, for the most recently issued projection covering this target month |
| `ipc_proj_scenario` | `ML1` (near-term) or `ML2` (medium-term) |
| `ipc_proj_issued` | Reporting date of that projection. **Leakage guard:** model features may only use projections with `ipc_proj_issued` ≤ the forecast origin. |
| `fewsnet_missing` | 1 if no classification of either kind |

Projections beyond the panel end (to 2027-05) stay in `data/processed/fewsnet/som.parquet`.

### WFP market prices
| Column | Description |
|---|---|
| `price_staple_anom` | Median across staple series in the unit of log(price) minus log(mean of the previous 12 months, at least 6 observations). Retail; cereals and tubers, oils, pulses. Each series stays in its own currency (SOS or SLS). This is a rolling baseline, not a seasonal one, because history is too short. |
| `price_staple_n_series` | Number of series behind the anomaly |
| `price_cereal_usd_kg` | Median retail USD price per kg of maize or sorghum |
| `price_n_markets` | Markets reporting in the unit-month |
| `wfp_missing` | 1 if no market reported |

### IOM DTM Somalia Emergency Trend Tracking
| Column | Description |
|---|---|
| `dtm_assessments`, `dtm_settlements` | Assessment rows and distinct settlements assessed |
| `dtm_arrivals`, `dtm_departures` | Sum of weekly new arrivals and departures (key-informant estimates) |
| `dtm_arrivals_conflict` / `_hazard` / `_other` | Arrivals by main cause. Conflict and natural hazard are kept separate. "Other" includes eviction. |
| `dtm_missing` | 1 if no settlement in the unit was assessed that month |

### VIEWS (forecast; never an observed outcome)
| Column | Description |
|---|---|
| `views_pred_fatalities` | `fatalities003` predicted fatalities, PRIO-GRID cells shared out by overlap area |
| `views_main_dich` | Area-weighted mean of the published `main_dich`. **Definition UNVERIFIED**, so do not display it until it is confirmed. |
| `views_issued` | Forecast run (2026-09) |
| `views_missing` | 1 if no forecast for the month |

The full 36-month horizon is in `data/processed/views/som.parquet`.

### Restricted: ACLED (not in `panel.parquet`)
`data/processed/restricted/panel_with_acled_som.parquet` (gitignored) is the same panel plus aggregated ACLED counts: `acled_events`, `acled_fatalities`, `acled_events_{battles,vac,remote,riots,protests,strategic}`, `acled_events_civtarget`, `acled_imprecise_events_country`, `acled_any`, `acled_missing`. Coverage covers only the complete months 2024-12 to 2025-10 (11 months). It is used under the standard ACLED terms for internal modelling only, and `acled_display = none`. See `DECISIONS.md`.

## Known limitations

1. **No conflict data after 2025-12.** Forecasts can be issued with a forecast origin of 2025-12 at the latest until GED Candidate (2026) files are added.
2. **Need inputs are sparse.** FEWS NET, prices and displacement cover fewer than 10% of months. The need index will only be meaningful for 2024-06 onwards, and properly only for 2026.
3. **No population layer**, so there are no per-capita rates and IPC weighting is by area.
4. **Missing SRP modules:** malnutrition (IPC AMN / SMART), outbreaks, HeRAMS facility functionality, immunisation, WASH, access, flood hazard and rainfall (CHIRPS). Each will join as an optional module.
5. Joins use coordinates or p-codes throughout, never names.
