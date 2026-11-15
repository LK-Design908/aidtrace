# Data inventory (Milestone 1)

Prepared 2026-11-14. All files were copied unchanged from the project owner's Downloads folder into `data/raw/`, `data/restricted/` and `docs/reference/`. Nothing has been modified, extracted or processed. Profiling was read-only: zips were streamed in place, not extracted.

**Headline (updated 2026-11-14, later the same day):** Somalia admin-2 boundaries have now been downloaded and verified (§1.0), and the toolchain is installed. The ACLED permission document is still missing. That does not block the Somalia panel, which can use UCDP alone, but it does block ACLED display and the with-ACLED model. See §4.

---

## 1.0 Spine: admin boundaries (added after first review)

| File | Format, size | Coverage | Spatial unit | Key columns | Notes |
|---|---|---|---|---|---|
| `raw/cod_ab/som/som_admin_boundaries.shp.zip` | Zipped shapefiles, 2.1 MB, 17 layers | Somalia COD-AB **v03**, valid_on 2025-01-08 (HDX metadata modified 2026-08-14). Downloaded from HDX 2026-11-14 | admin0 (1), admin1 (18 regions), **admin2 (91 districts)**. Also extended-coast (`_em`) variants, admin lines/points and OCHA operational zones (`opz1`, `opz2`) | `adm2_pcode` (e.g. SO1101), `adm2_name` (+ `name1–3` alt languages), `adm1_pcode`, `area_sqkm`, `valid_on`, `version` | Every layer has its `.shx`, `.dbf` and `.prj`. CRS **EPSG:4326**. 0 invalid geometries. p-codes unique. Names are all ASCII, so there are no encoding issues. **IOM DTM SOM district p-codes join at 100% (10,852/10,852 rows).** |
| `raw/cod_ab/som/som_admin_boundaries.xlsx` | XLSX, 139 KB | Same | p-code table | | Gazetteer for lookups |
| `raw/cod_ab/som/SOURCE.txt` | Text | | | | Source URL, licence, version, download date |

Points to decide:
- **Banadir (Mogadishu)** is split into 17 city districts of 0.5–143 km². One polygon has `adm2_pcode = "Unspecified"` (Banadir, 143 km², the area outside the named city districts). Options: (a) give it a synthetic code `SO22XX` and keep 91 units, recording this in `DECISIONS.md`; or (b) merge all Banadir districts into one Banadir unit for modelling, which is more stable given how imprecisely events in Mogadishu are geocoded. **Recommendation: (b) for the risk model, (a) for delivery records.**
- Verification script: `pipeline/check_cod_ab.py`. It is reproducible and does not modify `data/raw/`.

---

## 1. File-by-file inventory

### 1.1 Conflict

| File | Format, size | Coverage | Spatial unit | Key columns | Notes |
|---|---|---|---|---|---|
| `raw/ucdp/ged261-csv.zip` → `GEDEvent_v26_1.csv` | CSV in zip; 39 MB zipped, 274 MB unzipped | 1989-01-01 to **2025-12-31**. 417,968 events worldwide; **21,562 in the six SRP countries**: SOM 7,232, ETH 6,686, SDN 5,283, KEN 1,246, SSD 1,054, DJI 61 | Event points (lat/lon), `adm_1`/`adm_2` names (not p-codes), `priogrid_gid` | `id, type_of_violence, best/low/high, date_start/date_end, date_prec, where_prec, latitude, longitude, priogrid_gid, country` | `where_prec`: 1 = 9,525 events, 2 = 5,579, 3 = 2,207, 4 = 1,541, 5 = 2,641, 6–7 = 69. Codes 4–7 mean admin-1, a linear feature (road or river), country or international waters, so **~19% of Horn events cannot be reliably placed in a district** (Somalia: 646 of 7,232, ~9%). `date_prec` above 1 for 27%. Pre-2011 South Sudan events are coded under Sudan. **No GED Candidate (monthly 2026) files supplied.** |
| `raw/ucdp/ucdp-prio-acd-261-csv.zip` | CSV in zip, 46 KB | Conflict-year, v26.1 | Country | conflict_id, year, intensity | Context only. Not district-level. |
| `raw/ucdp/ucdp-onesided-261-csv.zip` | CSV in zip, 29 KB | Actor-year, v26.1 | Country | | Context only. |
| `raw/ucdp/ucdp-actor-261-csv.zip` | CSV in zip, 77 KB | Actor list, v26.1 | n/a | | Lookup for GED actor IDs. |
| `raw/ucdp/organizedviolencecy-261-csv.zip` | CSV in zip, 167 KB | Country-year, v26.1 | Country | | Context only. |
| `restricted/acled/ACLED Data_2026-11-14_..._2024-11-09_..._2025-11-07.csv` | CSV, 9.0 MB | 2024-11-09 to **2025-11-07**: **12 months only**. 12,894 events in Eastern, Middle and Northern Africa; **6,372 in SRP countries**: SDN 1,949, SOM 1,902, KEN 1,007, SSD 911, ETH 603, **DJI 0** | Event points, `admin1/admin2/admin3` names (no p-codes) | `event_id_cnty, event_date, time_precision, event_type, sub_event_type, actor1/2, inter1/2, admin1–3, latitude, longitude, geo_precision, fatalities, notes, tags, population_best` | `geo_precision` 1/2/3 = 4,436 / 1,866 / 70. `time_precision` 1/2/3 = 5,561 / 742 / 69. Event types: VAC 3,682; Explosions/Remote 1,138; Riots 611; Battles 572; Strategic dev. 221; Protests 148. **The period ends exactly 12 months before the export date**, which looks like the free/research-tier recency lag. Ethiopia's 603 events in 12 months is low compared with GED (≈1,800/yr recently) and may need checking. |

### 1.2 Benchmark

| File | Format, size | Coverage | Spatial unit | Key columns | Notes |
|---|---|---|---|---|---|
| `raw/views/fatalities003_2026_09_t01_cm.csv` | CSV, 345 KB | One forecast run (Sept 2026 data). `month_id` 562–597 = **2026-10 to 2029-09** (36 steps). 191 countries; all six SRP countries present (36 rows each) | Country-month (`gwcode`, `isoab`) | `main_mean, main_dich, main_mean_ln` | **Forecasts only, no history.** Benchmarking needs past VIEWS runs covering months for which outcomes exist (≤ 2025-12 in GED). This file can only be used as a forward-looking feature or a display layer. |
| `raw/views/fatalities003_2026_09_t01_pgm.csv` | CSV, 13.6 MB | Same run and months. 471,960 rows = 13,110 cells × 36 | PRIO-GRID cell (`pg_id`, 0.5°) | `main_mean, main_dich, main_mean_ln` | Same limitation. Cells need an area-weighted overlay onto admin-2. |

### 1.3 Need: food security (FEWS NET, IPC 3.1-compatible)

All files share one schema: `projection_start, projection_end, reporting_date, classification_scale, scenario (CS/ML1/ML2), value (phase 1–5), is_allowing_for_assistance, unit_type, country_code, datasourcedocument`. **Geometries are dissolved by phase, not by admin unit.** Each feature is the union of all areas in one phase, with separate `idp_camp` and `fsc_urban` point/polygon features. Mapping to admin-2 therefore needs an area-weighted overlay, giving the maximum phase and the share of area or population in Phase 3+.

| File | Country | Scenario | Period | Features | Notes |
|---|---|---|---|---|---|
| `horn_of_africa_202406_CS.kml` | ET, KE, SO | CS | 2024-06 | 17 | Phase 1–4 |
| `horn_of_africa_202406_ML2.kml` | ET, KE, SO | ML2 | 2024-10 to 2025-01 | 13 | Phase 1–4 |
| `SD_202607_ML1.kml` | SD | ML1 | 2026-07 to 2026-09 | 5 | Phase 2–4. No Sudan CS supplied. |
| `SD_202607.geojson` | SD | ML1 + ML2 | 2026-07 to 2027-01 | 10 | Superset of the KML. Has an `fsc_urban` unit type. |
| `SS_202606_CS/ML1/ML2.kml` | SS | CS, ML1, ML2 | 2026-06 to 2027-01 | 3/4/3 | Admin units (`fsc_admin`), not livelihood zones |
| `SS_202606.geojson` | SS | CS + ML1 + ML2 | 2026-06 to 2027-01 | 10 | Superset of the KMLs |
| `KE_202609_ML1/ML2.kml` | KE | ML1, ML2 | 2026-09 to 2027-01 | 4/5 | |
| `KE_202609.geojson` | KE | ML1 + ML2 | 2026-09 to 2027-01 | 9 | Includes Point/MultiPoint IDP features |
| `SO_202610_CS/ML1/ML2.kml` | SO | CS, ML1, ML2 | CS 2026-10; ML1 2026-10 to 2027-01; ML2 2027-02 to 2027-05 | 5/5/6 | **Somalia data are usable from the KMLs** |
| `SO_202610.geojson` | SO | (none) | (none) | **0** | **Empty FeatureCollection.** It is probably a failed export, and the KMLs cover it. |

Gaps: no Ethiopia or Djibouti classifications for 2026. No history between 2017 and 2024-05, or between 2025-02 and 2026-05, so the panel would have IPC values for roughly 4 of ~118 months. Projections (ML1/ML2) must be stored separately from current (CS) and never used as features for the months they project. KML/GeoJSON are nominally WGS84 (no `crs` member); confirm on load.

### 1.4 Need: market prices

| File | Format, size | Coverage (SRP countries only) | Spatial unit | Key columns | Notes |
|---|---|---|---|---|---|
| `raw/wfp_prices/wfp_food_prices_global_2025.csv` | CSV, 59.5 MB | 2025-01-15 to 2025-12-15. 29,159 rows: ETH 9,097, SOM 7,130, SSD 6,491, KEN 2,586, SDN 2,544, DJI 1,311 | Market point (lat/lon) with `admin1`/`admin2` **names** | `countryiso3, date, market, market_id, latitude, longitude, commodity, commodity_id, unit, pricetype, priceflag, currency, price, usdprice` | Mid-month dates. 125 commodities. **Mixed units** (KG, 100 KG, 3.5 KG, L, Head, Day…), to be normalised per commodity. `priceflag` mixes actual and aggregate. **Somalia mixes SOS and SLS** (Somaliland shilling), so use `usdprice` or convert per currency. Includes exchange-rate and wage series. |
| `raw/wfp_prices/wfp_food_prices_global_2026.csv` | CSV, 42.5 MB | 2026-01-15 to 2026-10-15. 23,690 rows | Same | Same | Same caveats. |

Gap: price anomalies need a rolling baseline. Two years of data cannot give a 2017+ panel or a seasonal baseline. The full WFP history from HDX (per-country files back to the 2000s) is needed.

### 1.5 Displacement

| File | Format, size | Coverage | Spatial unit | Key columns | Notes |
|---|---|---|---|---|---|
| `raw/iom_dtm/IOM_DTM_ETT_SOM_Tracker_sinceJune2026_w19.xlsx` | XLSX, 3.7 MB. Sheets: Full dataset by assessment (10,852 rows × 79 cols), Methodology, Contacts & Partners, Data collection week | June 2026 to week 19 (31 Oct–4 Nov 2026) | Settlement, with **OCHA p-codes** (`OCHA District Pcode`, e.g. SO2001) | Date of Assessment (Excel serial), Region/District + p-codes, Main Cause of Displacement (+ conflict type, natural hazard type), new arrivals, departures, origin, needs/response flags | Good p-code join. Cause is split into conflict vs natural hazard, as the brief requires. Weekly KII-based **estimates**, urban-focused coverage (see Methodology sheet). Only about 5 months long. |
| `raw/iom_dtm/IOM_DTM_SSD_EventTracking_Dataset_Jan-Sep 2026_20261021.xlsx` | XLSX, 0.9 MB. Sheets: Readme, Pivot, ET Dashboard, DTM_Event Tracking_Dataset (779 rows × 118 cols), Contacts & Partners, Trends | Assessment dates Jan–Oct 2026 (the filename says Jan–Sep; the Readme says Jan–Oct) | Payam/county with **p-codes** (`SS1101`, `SS110101`). Origin p-codes include Sudan (`SD08105`) | Event dates, affected group (IDP/returnee), individuals and demographics, Movement Trigger, conflict sub-type, origin and destination p-codes, sector severity | Includes cross-border returns from Sudan. Trigger separates conflict from disaster. |
| `raw/iom_dtm/Kenya_Displacement_Demographic_Data_2024_2025..xlsx` | XLSX, 0.3 MB. Sheets: Info1, Data (6,322 rows × 12 cols) | Collection dates 2024–2025 | Household survey: County / Sub-County / Ward **names only, no p-codes**. 5 counties (Garissa, Turkana, Mandera, Wajir, Tana River) | Date of collection, push_factor, pull_factor, household composition | **Publisher not stated in the file.** Household-level: it describes the profile of displaced households, not inflow volume. Name-only joins are not allowed (§4.1), so a name→p-code crosswalk must be built and reviewed. Double period in the filename. |

Gaps: no UNHCR PRMN (Somalia, long history with reasons), no DTM for Ethiopia, Sudan or Djibouti, and no displacement history before 2026 for Somalia or South Sudan.

### 1.6 Reference documents

| File | Notes |
|---|---|
| `docs/reference/ghoa-food-insecurity-and-health-crisis_who-strategic-response-plan.pdf` (974 KB) | WHO SRP Aug 2026–Jan 2027. `Downloads/who_ghoa_srp_2026.pdf` is byte-identical (same SHA-256), so only one copy is kept. |
| `docs/reference/BUILD_BRIEF.md` | Copy of the build brief. |
| `docs/reference/fewsnet_maps/1–10.webp` | FEWS NET map images: HoA CS Jun 2024, HoA ML2 Oct 2024–Jan 2025, Sudan ML1 Jul–Sep 2026 and ML2 Oct 2026–Jan 2027, South Sudan CS Jun 2026 and ML2 Oct 2026–Jan 2027, Kenya ML2 Oct 2026–Jan 2027, Somalia CS Oct 2026, ML1 and ML2. They duplicate the vector data and are kept for visual QA only. **An 11th image in the message could not be processed** (format/size error), so its content is unknown. |
| ACLED written permission | **Missing.** |
| UN Panel of Experts reports, OCHA access snapshots, 2023 diversion review | **Missing.** |

---


## 1.7 Added 2026-11-15 (owner-supplied)

| Files | Format, size | Coverage | Use | Notes |
|---|---|---|---|---|
| `raw/worldpop/ken_agesex/` (40 GeoTIFFs) | 3 arc-sec rasters, float32, EPSG:4326, 1.4 GB | Kenya, 2020 (year from the SSD table; KEN year UNVERIFIED) | Population, under-5, weight grid | 36 age/sex bands plus under-1/5/15 and women 15–49. No total band, so the total = sum of bands. |
| `raw/worldpop/ssd_agesex/` (42 files) | Same, 0.6 GB | South Sudan, 2020 | Same | Includes a `regions` raster and a proportions table |
| `raw/worldpop/som_buildings/` (10 GeoTIFFs + README) | 3 arc-sec, EPSG:4326, 134 MB | Somalia, imagery about 2009–2020 | Exposure proxy and weight grid; urbanisation | CC BY-SA 4.0 (share-alike) |
| `raw/worldpop/som_urban_extents/` | Shapefiles (with .prj) | Somalia: 23 urban extents, 21 named centres | Urban share | Licence UNVERIFIED |
| `raw/cow/nmc_v7/` | Zipped CSV/DTA + PDF documentation | Country-year 1816–2022, all six countries | Country context feature (lagged one year) | `-9` = missing |
| `raw/cow/midloc_2_1/` | CSV (Latin-1) + codebooks | Dispute- and incident-level points, 1992–2010; 152 incidents in the Horn | Static historical feature | Interstate disputes only, not local conflict |

Population for Somalia, Sudan, Ethiopia and Djibouti, and buildings for countries other than Somalia, are still missing. The loader picks up more countries as soon as files are listed in `pipeline.yaml` → `static`.

---

## 2. Match to expected roles

| Role | Expected dataset | Status | Comment |
|---|---|---|---|
| **Spine** | OCHA COD-AB admin-0/1/2 | **All six present** (Milestone 8): SOM v03 (91), ETH v04 (107 zones), KEN v01 (290), SSD v03 (79), SDN v03 (189), DJI GADM-derived 2022 (20) | Vintages are in DECISIONS.md. Djibouti's GADM licence is flagged in LICENCES.md. |
| Conflict outcome and predictor | UCDP GED (+ Candidate) | Present, to 2025-12 | **No 2026 data**, so current months cannot be scored. GED Candidate monthly files are needed. |
| Conflict predictor (restricted) | ACLED | Present, 12 months, ends 2025-11 | **Permission letter missing**, and too short to train a with-ACLED model alone. |
| Benchmark | VIEWS | Partial | One future-only run, so no historical benchmark is possible yet. |
| Need: IPC | IPC / FEWS NET / FSNAU | Partial | Snapshots only (2024-06/10, 2026-06 onwards). No ET or DJ for 2026. No history. |
| Need: prices | WFP / RTFP | Partial | 2025–2026 only. Baseline history missing. |
| Need/shock: rainfall | CHIRPS | **MISSING** | GEE connector is optional. Local monthly CHIRPS rasters (or a 1991–2020 climatology) are needed. |
| Displacement | IOM DTM, UNHCR PRMN | Partial | SOM since 2026-06; SSD 2026; KEN household survey. No PRMN. |
| Exposure | WorldPop | **Partial (added 2026-11-15):** population for KEN and SSD; buildings as a proxy for SOM | Population-weighted IPC and per-capita displacement where available. |
| Diversion proxies | PoE / 2023 review, hand-coded | MISSING (allowed) | Slot will be built empty. |
| Nutrition | IPC AMN; SMART GAM/SAM | **MISSING** | Drives the GAM/SAM triggers. |
| Health system | WHO HeRAMS | **MISSING** | Also the delivery-point list. Synthetic facilities will be used in demo mode. |
| Outbreaks | WHO AFRO/EMRO bulletins, EWARS/IDSR | **MISSING** | |
| Immunisation | DHS/MICS measles | **MISSING** (allowed) | |
| WASH | WASH cluster severity / JMP | **MISSING** (allowed) | |
| Access | OCHA access snapshots | **MISSING** | |
| Flood hazard | GloFAS / JRC GSW / national | **MISSING** | Needed for El Niño pre-positioning. |

**Files that fit no role:** none. Every supplied file is identified. The Kenya household survey fits "displacement" only weakly (composition and drivers, not flows).

---

## 3. Data-quality flags for the pipeline

1. **Coverage mismatch with a 2017-01 panel start.** GED is the only source spanning 2017 onwards. IPC, prices and displacement are present for 1–2 years at most. The need index will be almost entirely "missing" before 2024 unless history is added.
2. **No conflict data after 2025-12.** GED ends 2025-12-31 and ACLED ends 2025-11-07. The model can be trained and validated, but the "current" forecast would be 11 months stale.
3. **Imprecise GED events.** ~19% of Horn events are located to admin-1 or worse. These go in a separate `*_imprecise` count and are not assigned to an arbitrary district.
4. **No p-codes in GED, ACLED, WFP or Kenya displacement.** All are names or coordinates. Use a point-in-polygon join on coordinates where precision allows, never a name join.
5. **Somalia currencies:** SOS and SLS coexist.
6. **FEWS NET phase-dissolved geometries** need an area/population-weighted overlay onto admin-2.
7. **Sudan/South Sudan boundaries:** origin p-codes in the SSD DTM (`SD08105`) imply a specific Sudan COD vintage. Pick the matching COD-AB vintage and record it.
8. **Excel dates** are serial numbers (e.g. 46330 = 2026-11-04). Convert on load.

---

## 4. Blockers and decisions needed before Milestone 2

1. ~~Admin-2 boundaries~~ **Resolved for Somalia**: downloaded from HDX with your approval and verified (§1.0). Still to decide: how to handle Banadir's "Unspecified" polygon.
2. **ACLED permission document.** Not supplied, so `acled_display = none` and standard-terms handling apply until it is. Please also confirm whether a longer, unlagged export is possible: 12 months is too short for the with/without-ACLED comparison.
3. ~~No toolchain~~ **Resolved**: Python 3.11.9, Node 24.20 LTS and Git 2.56 were installed via winget. Environment caveats, which are recorded in `README.md`:
   - **HTTPS inspection:** pip fails certificate checks unless it uses the Windows certificate store (`--use-feature=truststore`). Verification stays on.
   - **Windows Application Control (likely Smart App Control)** blocks some newer compiled Python packages: pandas 3.x and pyogrio 0.12. Versions pinned to pandas 2.2.3 and pyogrio 0.10.0 load fine. Expect this again with LightGBM, rasterio and others. The security setting has not been changed.
   - **Path length:** the session folder path is too deep for some DLLs, so the venv lives at `%USERPROFILE%\.venvs\aidtrace`. Moving the repo to a short permanent folder fixes this.
4. **Workspace location.** The repo currently sits in a temporary folder the app made for this session. Pick a permanent folder before real work starts.
5. **Data to add if possible**, in priority order for the Somalia pilot: COD-AB → WorldPop (SOM) → CHIRPS monthly (1991 onwards, HoA clip) → UCDP GED Candidate 2026 → FEWS NET/IPC history 2017 onwards (SO) → WFP Somalia price history → UNHCR PRMN → historical VIEWS runs (for benchmarking) → HeRAMS → IPC AMN/SMART → outbreak bulletins → flood hazard.
6. **Kenya displacement file:** who published it, and under what terms?
