# Build Brief: Aid Verification and Diversion-Risk Platform (Beta)

Working title: **AidTrace**. Rename freely.

You are building a working beta of a humanitarian aid platform for the Horn of Africa. Read this entire brief before writing code. Work in the milestones at the end, in order, and stop for review at the end of each one. Where this brief conflicts with convenience, the safety and licensing constraints in Section 2 win.

---

## 1. Purpose

Humanitarian supplies (initially WHO medical supplies, but the design must be supply-agnostic) are often diverted by local gatekeepers, officials or armed groups before reaching the people they were meant for. The platform does two things:

1. **Verification.** A confidential, offline-capable channel through which a trusted recipient confirms that a specific consignment arrived intact, with a photo and a secret PIN. A second, pre-agreed duress PIN appears to succeed normally but silently alerts the sending organisation's security focal point that the recipient is under pressure.
2. **Risk prioritisation.** A distributor-side map that combines historical conflict, food insecurity, climate, price and displacement data with the organisation's own field reports. It estimates, for each district and month, (a) humanitarian need and (b) the risk of diversion or violence. These estimates indicate *how* aid should be delivered (extra verification, protection, modality changes), not *whether* people in need receive it.

There are two user roles: **Distributor** (headquarters and field staff of the sending organisation) and **Recipient** (vetted staff at delivery points).

### 1.1 Operational context: WHO Strategic Response Plan
The design is anchored to the *WHO Greater Horn of Africa Food Insecurity and Health Crisis Strategic Response Plan, August 2026–January 2027* (the SRP; a copy is in `docs/reference/`). Relevant facts the app must reflect:

- **Countries:** Djibouti, Ethiopia, Kenya, Somalia, South Sudan, Sudan. These six define the map extent and the country list everywhere in the app.
- **Scale:** 37.8 million people facing acute food insecurity; 4.9 million children projected to suffer acute malnutrition, including 1.5 million needing treatment for severe acute malnutrition (SAM).
- **Targeting:** the response prioritises IPC Phase 3–5 areas, and gives operational priority to areas with SAM, low immunisation coverage, active outbreaks, displacement, poor WASH, and insecurity or limited access (SRP §4.3).
- **Supplies:** emergency health kits, cholera kits, SAM supplies, essential medicines, vaccines and nutrition commodities, often **pre-positioned** ahead of flooding or access disruption (SRP §4.4.1, §5.1).
- **Delivery points:** health facilities, stabilisation centres, and mobile health and nutrition teams. In this beta, a **Recipient is a facility or team account**, not a household.
- **Climate:** forecast El Niño conditions (August–October 2026, possibly persisting into early 2027) raise flood risk and water- and vector-borne disease risk (SRP §1.1).
- **Budget:** US$28.0 million in total, of which US$5.5 million is for emergency supplies, logistics, operational readiness and health kits, and US$2.7 million for cross-cutting functions, including information management, M&E and accountability to affected populations (SRP Table 1). The app supports those two budget lines; its outputs should be framed in their terms.
- **Operational triggers** (SRP §7): deterioration in IPC classification or expansion of Phase 4–5 areas; GAM or SAM rates above emergency thresholds; outbreak alerts; rising crude or under-five mortality; deterioration in health facility functionality; large-scale displacement; significant deterioration in access; new climate shocks.
- **Monitoring indicators** the app can feed (SRP §7): availability of essential medicines, vaccines and nutrition commodities; timeliness of operational reporting; humanitarian access constraints affecting delivery; coverage of mobile services.

---

## 2. Hard Constraints (read first, do not relax)

### 2.1 Humanitarian principles
- Need determines allocation. Risk scores must never be presented as a reason to withhold aid from a high-need area. Recommendations should change delivery modality, verification intensity or protection, not exclude people.
- The map works at **admin-2 (district) resolution**. Do **not** build sub-district or sub-town prediction of where armed groups are located, hide or wait. That output is dual-use targeting information, and a leak would endanger civilians and staff. If finer resolution is later wanted, it requires an explicit decision by the project owner, recorded in `DECISIONS.md`.
- Do not build a "decoy shipment" feature in this beta. Note it in `DECISIONS.md` as deferred pending a policy decision. Instead, support established **low-profile logistics** options that a shipment can be flagged with: unbranded packaging, split consignments, varied delivery days and routes, smaller and more frequent deliveries, and pre-positioning before lean seasons or flood periods. The recommendation engine (Section 5.4) may suggest these.

### 2.2 Data licensing
- **ACLED:** the project has **written permission from ACLED** for this use. A copy of the permission is in `docs/reference/ACLED_permission.*`. Read it before writing any ACLED code, and summarise its exact scope in `data/LICENCES.md`: permitted uses, permitted displays, whether event-level data may be shown to authenticated users, any restrictions on sharing or exports, and the attribution required.
  - Where the permission is silent on a point, the standard ACLED terms apply. Under those terms, outputs must be transformed so that ACLED events cannot be reconstructed from them, and no ACLED data may be exposed to third parties.
  - Implement ACLED display as a **permission-gated layer**. One config block (`acled_display: none | aggregated | event_level`, plus the roles allowed to see each level) controls what the UI and API return. Set it to the most permissive level the written permission clearly allows, and no further.
  - Regardless of the permission, ACLED-derived content is never included in the Health Cluster export, the public demo, or any unauthenticated route, and is never shown to Recipient accounts. Store raw ACLED files in `data/restricted/`, excluded from git.
  - Show the ACLED attribution wherever ACLED-derived content appears.
- Keep the system able to run with ACLED switched off (config flag `USE_ACLED`, default `true`), with UCDP GED as the fallback conflict source. Permissions can be withdrawn, and the comparison is informative. Report model performance both with and without ACLED.
- Create `data/LICENCES.md`: one row per dataset with its source URL, licence, commercial use (yes/no), ML-use restrictions, redistribution limits and required attribution. Fill it from the licence files or terms that accompany each download; where unknown, write "UNVERIFIED" and flag it to me. Display attributions in the app footer.
- The organisation's own field reports are stored in the app's own database. They are never merged into, or written back to, any third-party dataset.

### 2.3 Recipient safety
- No feature may reveal, on the recipient's device or in its network traffic, whether a duress PIN was used. See Section 6.3 for the required design.
- Strip all EXIF and location metadata from photos on the device before storage or upload.
- The recipient app stores no readable history of past reports.
- Duress alerts go to a configurable **organisation security focal point**, never to local "authorities", which in some areas are the threat.

### 2.4 Beta data
- The public demo (landing page, role switcher) runs on **synthetic** shipments, recipients and reports only. Real field reports and real recipient accounts must never be reachable from demo mode.

---

## 3. Architecture

Monorepo:

```
aidtrace/
  data/
    raw/           # downloaded datasets, one folder per source (provided; never modified)
    restricted/    # ACLED and anything licence-restricted (provided; never modified; gitignored)
    processed/     # cleaned/clipped data, hand-coded indicators, panel, model outputs
    LICENCES.md
  pipeline/        # Python: ingestion, panel construction, model, scoring
  server/          # Python FastAPI: auth, shipments, reports, risk API
  web/             # Distributor web app: React + Vite + MapLibre GL + deck.gl
  recipient/       # Recipient PWA: React, offline-first, installable
  docs/
    reference/     # WHO SRP, ACLED permission, PDF reports (provided)
  DECISIONS.md
  README.md
```

Stack defaults (change only with a reason recorded in `DECISIONS.md`):
- **Pipeline:** Python 3.11, pandas, geopandas, shapely, rasterio/xarray (for rainfall rasters), scikit-learn, LightGBM, pyarrow.
- **Server:** FastAPI, SQLite for the beta (with a clean path to PostgreSQL/PostGIS), SQLAlchemy, Pydantic, argon2 for password hashing, PyNaCl (libsodium) for signing and sealed boxes.
- **Distributor web:** React, TypeScript, Vite, MapLibre GL JS, deck.gl (`ColumnLayer` or `GeoJsonLayer` with extrusion for 3D; 2D choropleth as the default view), a free basemap that needs no API key (for example OpenFreeMap or MapLibre demo tiles), with a fallback to plain admin boundaries if tiles fail.
- **Recipient app:** a Progressive Web App (React, TypeScript, service worker, IndexedDB), using ZXing or `html5-qrcode` for scanning and `libsodium-wrappers` for crypto. Target an installed size under **3 MB** excluding cached tiles. It must work fully offline after first install.

---

## 4. Data

### 4.0 Files provided
The project owner supplies, before you start:
- `data/raw/<source>/`: one folder per dataset, as downloaded, with any README, codebook or data dictionary alongside, and where useful a `SOURCE.txt` giving the source URL and download date.
- `data/restricted/acled/`: the ACLED download (Section 2.2).
- `docs/reference/`: the WHO SRP PDF, the ACLED written permission, and any PDF reports (UN Panel of Experts, OCHA access snapshots, the 2023 diversion review).

Handling rules:
- **Never modify files in `data/raw/` or `data/restricted/`.** All cleaned, clipped or converted outputs go to `data/processed/`, produced by scripts, so that every step is reproducible from the originals.
- Accept any common format: CSV, XLSX, JSON, Parquet, Stata/SPSS; GeoJSON, GeoPackage, shapefiles, Esri geodatabases; GeoTIFF or NetCDF rasters; zip archives (read in place or extract to `data/processed/`, not both).
- **Shapefiles:** check that every `.shp` has its `.shx`, `.dbf` and `.prj` files. If a `.prj` is missing, stop and report it rather than assuming a projection. Reproject everything to EPSG:4326 for storage and to a suitable equal-area projection for any area calculation.
- **Rasters:** if CHIRPS or other rasters are global or daily, clip them to the bounding box of the six SRP countries (with a small buffer) and aggregate to monthly before further processing. Write the clipped versions to `data/processed/`. Watch memory use; process in chunks.
- **Codebooks:** read them before writing loaders. Pay particular attention to missing-value codes, ACLED `geo_precision` and `time_precision`, UCDP best/low/high fatality estimates, IPC current vs projected classifications, and price units and currencies.
- **PDF reports are not datasets.** Extract their tables where possible, but treat any district-month indicators coded from PDFs as hand-coded data. Write them to `data/processed/handcoded/` with a column citing the document and page for every value, and list them for the project owner to verify.
- Check text encodings (Somali, Amharic and Arabic place names) and normalise to UTF-8.

### 4.1 First task: inventory
Before writing any pipeline code:
1. List every file in `data/raw/`, `data/restricted/` and `docs/reference/`, with its format, size, date range, spatial unit and key columns. Note missing sidecar files, missing codebooks and anything you cannot identify.
2. Match each file to the roles below. Report any expected dataset that is missing, and any file you cannot identify.
3. Write the inventory to `docs/DATA_INVENTORY.md` and stop for my review.

Expected datasets and roles:

| Role | Dataset | Notes |
|---|---|---|
| Spine | OCHA COD-AB admin boundaries (admin-0/1/2) for each Horn country present | Join everything on admin-2 **p-codes** |
| Conflict outcome and predictor | UCDP GED (+ monthly Candidate files if present) | Open licence; the core conflict source |
| Conflict predictor (restricted) | ACLED | See Section 2.2 |
| Benchmark | VIEWS forecasts (PRIO-GRID-month and/or country-month) | Used to benchmark, and optionally as a feature |
| Need | IPC / FEWS NET / FSNAU phase classifications | Phase by area and period; handle "projected" vs "current" separately |
| Need | WFP and/or World Bank RTFP market food prices | Market points: assign to admin-2; compute price anomalies vs a rolling baseline |
| Need / shock | CHIRPS rainfall (rasters or extracted series) | Compute monthly anomalies vs a 1991–2020 baseline per admin-2 |
| Displacement | IOM DTM, UNHCR PRMN | PRMN includes displacement reasons; keep conflict vs drought separate |
| Exposure | WorldPop population | For per-capita rates |
| Diversion proxies | Any hand-coded file from UN Panel of Experts reports or the 2023 diversion review | May be absent; build the slot anyway |
| Nutrition (SRP) | IPC Acute Malnutrition (AMN) phases; SMART survey GAM/SAM estimates | Drives SRP malnutrition triggers |
| Health system (SRP) | WHO HeRAMS (health facility functionality and service availability) | Facility locations double as delivery points |
| Outbreaks (SRP) | WHO AFRO/EMRO weekly bulletins, EWARS/IDSR extracts where shared | Cholera, measles, malaria, dengue alerts by area |
| Immunisation (SRP) | Subnational measles coverage (DHS/MICS or national programme data) | Often national-only; allow missing |
| WASH (SRP) | WASH cluster severity or JMP indicators | Often coarse; allow missing |
| Access (SRP) | OCHA humanitarian access snapshots / access-constraint reports | Hand-code to district-month if only PDFs exist |
| Flood hazard | Flood extent or hazard layers (for example GloFAS, JRC Global Surface Water, or national flood maps) | For El Niño pre-positioning |

The SRP rows may not all be in `data/raw/`. Report which are present; build each as an optional module that the need index uses when available.

Begin with **Somalia** as the pilot country; generalise to the other five SRP countries present in `data/raw/` once the Somalia pipeline works. Countries differ in boundary vintages and p-code formats; never join on names alone. Sudan and South Sudan in particular have contested and recently changed admin-2 boundaries: document which version you use.

### 4.2 Panel construction
- Unit of analysis: admin-2 × calendar month, from 2017-01 to the latest month available.
- Build an empty spine first (every district × every month), then left-join each source. Represent missing data explicitly (NaN plus a `*_missing` indicator); never silently fill with zero. Zero violence and unrecorded violence are different things, and remote areas are under-reported.
- Store the panel as `data/processed/panel.parquet` with a data dictionary in `docs/PANEL.md`.

### 4.3 Updating
- Write each source's loader as an independent module with a `refresh()` function that detects whether a newer local file exists and rebuilds only the affected parts.
- CHIRPS live ingestion via Google Earth Engine (`UCSB-CHC/CHIRPS/V3/DAILY_SAT`) is an **optional connector**: it requires an Earth Engine account and a service-account credential supplied through environment variables. If credentials are absent, fall back to local files without error. Never call Earth Engine from the browser.
- A scheduled job (simple cron or APScheduler in the beta) runs the refresh and rescoring monthly, and logs what changed.

---

## 5. Risk Model

### 5.1 Two outputs, kept separate
1. **Need index** (per district-month): a transparent composite aligned with the SRP prioritisation criteria: IPC phase (with Phase 3–5 as the eligibility gate), acute malnutrition (IPC AMN / GAM / SAM), active outbreaks, health facility functionality, displacement inflow, immunisation coverage, WASH, rainfall anomaly, flood hazard and food price anomaly. Use documented weights, not a black box, and renormalise across whichever components are available. Show components and missing inputs on hover.

1a. **Trigger engine.** Implement the SRP §7 operational triggers as explicit rules evaluated each month per district, each producing a dated alert with its evidence: IPC deterioration or new Phase 4–5 area; GAM/SAM above emergency thresholds; outbreak alert; rising CMR or U5MR; deterioration in facility functionality; large displacement inflow; access deterioration; climate shock (extreme rainfall or flood anomaly). Store threshold values in a config file, not in code, and cite their source in `docs/TRIGGERS.md`. Standard humanitarian thresholds (for example GAM ≥15%, CMR ≥1 per 10,000 per day, U5MR ≥2 per 10,000 per day) are a starting point; flag them for confirmation by the project owner.
2. **Diversion/violence risk** (per district-month, forecast 1 and 3 months ahead): the probability of at least one fatal conflict event (UCDP) in the target month, plus a secondary model for any recorded event if ACLED is enabled.

### 5.2 Method
- Baselines first: (a) last month's value; (b) logistic regression on lagged conflict history. Then LightGBM with lagged features from all sources (lags 1, 3, 6, 12; rolling means; spatial lags from neighbouring districts).
- **Validation must be strictly temporal**: expanding-window training, testing on later months only. No random splits, no leakage of future data into features (be especially careful with IPC projections and revised datasets).
- Report AUC, Brier score, precision/recall at a fixed alert rate, and calibration plots, for each model and with/without ACLED. Benchmark against VIEWS for the same months and areas, where the units can be aligned.
- Field reports and diversion proxies enter as features only once there are enough of them; until then, show them as a separate map layer and do not let them move the model.
- Write results to `docs/MODEL_REPORT.md`, including an honest statement of where the model fails. Local conflict is hard to predict; a model that does not beat simple baselines must say so.

### 5.3 Presenting uncertainty
- Display risk as bands (low / elevated / high / severe), with the probability and a confidence interval on hover.
- Every risk display carries the last data date and a note that scores are estimates.

### 5.4 Priority view
Combine the two outputs into a 2×2 view per district:
- High need, lower risk: standard delivery.
- High need, higher risk: deliver with enhanced verification and protection (more scan points, independent monitoring, staggered or cash-based delivery).
- Lower need, higher risk: monitor.
- Lower need, lower risk: routine.

The "recommendations" panel generates text from this view, the active triggers and the district's risk components (for example: "IPC Phase 4; cholera alert; elevated diversion risk; 3 field reports of extortion at handover in the last 90 days; recommend two-point verification, split consignment, and an independent post-distribution check"). Where flood hazard and the season indicate likely access loss, recommend pre-positioning and name the nearest lower-risk functional facility from HeRAMS as a candidate hub. Recommendations must never suggest withholding aid.

---

## 6. Applications

### 6.1 Landing page (beta only)
- Explains the purpose in plain language, states that the demo uses synthetic data, and shows the data attributions.
- A clearly labelled role switcher opens the Distributor or Recipient experience in demo mode, so a reviewer can follow one consignment end to end: create shipment, print QR, scan as recipient, submit (normal or duress), see the result on the distributor side.

### 6.2 Distributor app
Authentication: username plus password (argon2), separate account type from recipients, session timeout, and role-based access (HQ admin, security focal point, field staff). Duress alerts are visible only to security focal points and HQ admins.

**Tab 1: Map**
- Interactive map of the Horn of Africa at admin-2 level.
- Toggleable layers, each with its own legend: need index; diversion/violence risk (1- and 3-month); IPC phase; rainfall anomaly; food price anomaly; displacement inflow; UCDP fatalities aggregated by district-month (open data); ACLED conflict layer at the level set by `acled_display` (Section 2.2), with sub-filters for event type and actor category where permitted; organisation field reports; confirmed and failed deliveries. Even if event-level display is permitted, show points only at the precision ACLED itself records (respect its `geo_precision` field), and never at finer resolution than admin-2 in any derived layer (Section 2.1).
- A 2D/3D toggle: in 3D, extrusion height encodes one chosen variable and colour encodes another. Default to 2D, which is easier to read accurately.
- A time slider over months, with the forecast months visually distinct from observed months.
- Clicking a district opens a side panel: component values, risk trend, recent field reports, recent deliveries, and the generated recommendation.

**Add report** (from the Map tab): a form to log an incident (type: diversion, extortion at handover, theft in transit, violence near site, access denied, other; date; district; free text; severity; reporter; optional attachment). Reports are stored in the app database, appear on the field-report layer immediately, and are marked by the reporter's role. Free text and attachments are visible only to authorised roles. Include a review status (unverified / verified / rejected).

**Tab 2: Resource transfer**
- Create a shipment: contents chosen from an SRP item catalogue (emergency health kit, cholera kit, SAM medical supplies, essential medicines, vaccines, nutrition commodities, other), with quantities, batch numbers and expiry dates; whether cold chain is required; origin; destination district and site (selectable from HeRAMS facilities where available); assigned recipient account(s); expected delivery window; low-profile logistics flags (Section 2.1); and **scan points** (dispatch, handover, final distribution; at least two for high-risk districts as set by the priority view).
- **Two-person confirmation** for high-risk districts: receipt requires submissions from two separate recipient accounts at the site (for example the facility in-charge and a second staff member), each with their own PINs. A single submission leaves the shipment "partially verified".
- Generate a signed QR code per scan point (Section 6.3.1) and a printable label (PDF) to attach to the consignment.
- Shipment dashboard: status at each scan point; time of receipt; quantity confirmed vs dispatched; photo; field reports linked to the destination district; and, for authorised roles only, the duress status. For unauthorised roles, a duress submission must look identical to a normal successful one.
- Flags: overdue shipments, quantity shortfalls, PIN failures, receipts far outside the expected window, cold-chain failures, near-expiry stock.
- **Consumption reconciliation** (the check that supplies reached patients, not merely the facility): where facility service data are available (for example SAM admissions, cholera cases treated, vaccinations given), compare quantities delivered against expected consumption using configurable consumption ratios per item. Flag sites where deliveries persistently exceed plausible use, or where stock-outs are reported despite recent confirmed deliveries. Ratios live in config and are documented in `docs/RECONCILIATION.md`.
- **Independent verification queue:** randomly select a configurable share of confirmed deliveries (more in high-risk districts) for follow-up by a third-party monitor, and record the outcome. Random selection means no site can predict which deliveries will be checked.

**Tab 3: Indicators**
- A dashboard and CSV export of SRP-aligned indicators derived from the app: share of shipments verified on time; median dispatch-to-receipt time; quantity shortfall rate; stock availability by item and district; access-constraint reports; trigger alerts by district; coverage of facilities and mobile teams receiving supplies. Each indicator names the SRP §7 indicator it supports.
- **Health Cluster sharing:** an export of aggregated, non-sensitive delivery and indicator data (no field-report text, no duress information, no recipient identities, no ACLED-derived scores) suitable for the Health Cluster and national incident management systems.

### 6.3 Recipient app
A single function: scan a consignment and confirm receipt.

Flow:
1. First run (online, done during vetting): the recipient's device is enrolled. It receives the organisation's public signing key and the server's public encryption key, and an account identifier. **No PINs are stored on the device in any form**, not even as hashes.
2. Scan the QR code (works offline).
3. The app verifies the QR signature offline and shows the expected contents.
4. The recipient enters the quantity received per item, records cold-chain status where relevant (for vaccines, the vaccine vial monitor stage and whether temperature indicators were triggered), notes any damaged or expired items, takes a photo (compressed to about 100 KB, EXIF stripped), and enters their PIN.
5. The app shows the same "Receipt recorded" confirmation regardless of what PIN was entered.
6. The report is queued and sent automatically when signal returns, with retry and exponential backoff.

#### 6.3.1 QR payload
- Compact payload: shipment ID, scan-point ID, short manifest, destination code, expiry, signed with Ed25519 by the server. Keep it small enough for a reliably scannable QR at label size (target version 10 or lower; use a binary/base45 encoding if needed).
- The recipient app rejects QR codes with invalid signatures or past expiry, and shows a neutral "Code not recognised" message.

#### 6.3.2 PIN and duress design (required)
- Each recipient has a normal PIN and a duress PIN, set at enrolment and held **only on the server** (argon2 hashes).
- The device never checks the PIN. It places the entered PIN, the receipt data, the photo and a timestamp into one payload and encrypts it to the server's public key with a libsodium **sealed box**. Nothing on the device can reveal which PIN was entered, even under forensic inspection.
- Pad every payload to a fixed size bucket so that normal and duress submissions are indistinguishable in size, and send them on the same schedule.
- Server-side, decrypt and classify: normal PIN → receipt confirmed; duress PIN → receipt shown as confirmed to general staff, while an alert is raised immediately to the security focal point (in-app plus a configurable email or webhook); wrong PIN → receipt marked "unverified, PIN mismatch" for follow-up (this covers typos, without the device ever knowing).
- After successful upload, delete the queued payload from the device. Keep no local list of past reports.
- The plan originally proposed a spoken voice password. Do not implement a spoken secret: speaking the password aloud lets anyone present, including the person applying pressure, hear it. An optional short voice note may be attached as additional evidence, but it is never the authentication factor.

#### 6.3.3 SMS fallback
Where data signal is unavailable for long periods but SMS works, the app can generate a short **receipt code** (a compact, authenticated encoding of shipment ID, scan point, quantity summary and an encrypted PIN component, about 30–40 characters) that the recipient sends by SMS to a configured number. The server decodes it into a receipt. It must preserve the duress design: normal and duress codes must be the same length and indistinguishable. The photo follows later by data when signal allows. Build the encoder, decoder and a mock SMS gateway; integrating a real gateway (for example Africa's Talking) is out of scope for the beta.

#### 6.3.4 Feedback and safeguarding
The SRP requires community complaints and feedback mechanisms, and PSEAH safeguards (SRP §6). The recipient app may include a simple "Report a problem" option for supply issues (shortage, theft, demands for payment, access blocked), which creates a field report. It must **not** collect sexual exploitation or abuse allegations in free text. Instead, show the organisation's confidential PSEAH reporting contact, configurable per country, so that such reports reach trained staff through a survivor-centred channel.

#### 6.3.5 Offline and size
- Service worker caches the app shell; IndexedDB holds the encrypted queue.
- Test on a low-end Android device profile and slow 3G emulation. The app must be usable after install with the network fully disabled.
- Record in `DECISIONS.md` whether a native wrapper (Capacitor) is needed later for background sync on iOS, which restricts PWAs.

---

## 7. Security and Privacy Baseline
- All traffic over HTTPS; secrets via environment variables; nothing sensitive committed to git.
- Server signing and encryption keys generated by a setup script and stored outside the repo; document key rotation.
- Rate-limit login and report endpoints; lock accounts after repeated failures.
- Audit log of who viewed duress alerts and field-report details.
- Data retention: photos and raw report payloads deleted after a configurable period (default 180 days), keeping only the derived receipt status.
- Write `docs/THREAT_MODEL.md` covering: seized recipient phone; coerced recipient; compromised distributor account; insider at HQ; leaked risk map; forged QR; replayed submissions (include a nonce and reject replays).

---

## 8. Testing and Acceptance
- Unit tests for every data loader (row counts, date coverage, p-code join rates; fail loudly if join rate falls below 95%).
- Tests proving that each ACLED display level returns exactly what its configuration allows, to the roles allowed, and that ACLED-derived content never appears in the Health Cluster export, demo mode, unauthenticated routes or Recipient views.
- Tests proving normal and duress payloads are the same size and that the device holds no PIN material.
- End-to-end test in demo mode: create shipment → scan → submit normal → confirmed; submit duress → appears confirmed to field staff, alert visible to focal point only; submit wrong PIN → flagged.
- Model evaluation reproducible from one command.

---

## 9. Milestones (stop for review after each)
1. **Data inventory** (`docs/DATA_INVENTORY.md`, `data/LICENCES.md`). Stop.
2. **Panel** for Somalia (`panel.parquet`, `docs/PANEL.md`, loader tests).
3. **Model** with baselines, LightGBM, with/without ACLED, VIEWS benchmark (`docs/MODEL_REPORT.md`).
4. **Server** (auth, shipments, signed QR, sealed-box reports, risk API, field reports).
5. **Distributor web app** (map, layers, 2D/3D, time slider, district panel, add report, resource transfer).
6. **Recipient PWA** (offline scan, verification, queue, duress design).
7. **Landing page and demo mode** with synthetic data; end-to-end tests; threat model; README with setup instructions.
8. Extend the pipeline to the other five SRP countries present in `data/raw/`.

Note on timing: the SRP period ends in January 2027, so this beta will not serve the current response operationally. Build it as a pilot for a successor plan or a continuation appeal, and make the SRP-specific content (countries, triggers, item catalogue, indicators) configurable rather than hard-coded.

## 10. Ask me before
- Changing resolution below admin-2, or adding any decoy or militia-location feature.
- Setting `acled_display` to any level where the written permission is ambiguous.
- Adding any third-party service that receives recipient data.
- Any change to the duress design.
