# Data licences

Status: **draft for project-owner review** (Milestone 1, 2026-11-14).

None of the files supplied came with a licence file, terms of use or `SOURCE.txt`. Under brief §2.2, any point not confirmed from accompanying terms is marked **UNVERIFIED**. The "Expected terms" column records what the publisher's standard terms are understood to say. It is a lead for checking, not a verified statement, and nothing below should be relied on until you confirm it.

| Dataset | Files | Source URL (expected) | Licence | Commercial use | ML-use restrictions | Redistribution limits | Required attribution | Status |
|---|---|---|---|---|---|---|---|---|
| OCHA COD-AB Somalia v03 (admin 0–2) | `raw/cod_ab/som/*` | https://data.humdata.org/dataset/cod-ab-som | **CC BY-IGO** (from HDX dataset metadata at download, 2026-11-14) | Yes (CC BY-IGO permits commercial use) | None stated | Redistribution allowed with attribution | "OCHA Somalia; OCHA Field Information Services Section (FISS), via HDX" | **Verified from HDX metadata** |
| OCHA COD-AB Ethiopia v04, Kenya v01, South Sudan v03, Sudan v03 | `raw/cod_ab/{eth,ken,ssd,sdn}/*` | https://data.humdata.org/dataset/cod-ab-{iso3} | **CC BY-IGO** (HDX metadata, downloaded 2026-11-15) | Yes | None stated | Allowed with attribution | "OCHA, via HDX" (see each folder's SOURCE.txt for the publishing office) | **Verified from HDX metadata** |
| Djibouti admin boundaries (GADM-derived, 2022) | `raw/cod_ab/dji/*` | https://data.humdata.org/dataset/cod-ab-dji | HDX lists **CC BY-IGO**, but the geometry is **derived from GADM**, whose own licence allows academic and non-commercial use only and forbids redistribution without permission | **UNVERIFIED (possibly no)** | UNVERIFIED | **UNVERIFIED: GADM terms may restrict it** | "OCHA / GADM, via HDX" | **Flag: check GADM terms before any commercial use or redistribution** |
| WorldPop age/sex population 2020, v2.0 (Kenya, South Sudan) | `raw/worldpop/{ken,ssd}_agesex/*` | https://www.worldpop.org (age/sex structures) | UNVERIFIED (WorldPop releases are normally **CC BY 4.0**); no licence file supplied | UNVERIFIED (expected yes) | None known | UNVERIFIED (expected: with attribution) | Expected: "WorldPop (www.worldpop.org), University of Southampton" + dataset DOI | UNVERIFIED |
| WorldPop gridded building patterns v1.1 (Somalia) | `raw/worldpop/som_buildings/*` | doi:10.5258/SOTON/WP00677 | **CC BY-SA 4.0**, per the supplied README | Yes | None stated | **Share-alike:** derived products that are *distributed* (e.g. building-derived district attributes) must carry the same licence | Dooley, C.A., Boo, G., Leasure, D.R. & Tatem, A.J. (2020), *Gridded maps of building patterns throughout sub-Saharan Africa, v1.1*, University of Southampton; building footprints "Ecopia Vector Maps Powered by Maxar Satellite Imagery" © 2020 | **Verified from README**; share-alike flagged |
| WorldPop urban extents ("SocialDistancing" v1.0, Somalia) | `raw/worldpop/som_urban_extents/*` | WorldPop | UNVERIFIED (no licence file; WorldPop default CC BY 4.0) | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: "WorldPop" | UNVERIFIED |
| Correlates of War: National Material Capabilities v7 | `raw/cow/nmc_v7/*` | https://correlatesofwar.org/data-sets/national-material-capabilities/ | UNVERIFIED (COW data are free to use with citation; documentation supplied but no licence statement checked) | UNVERIFIED | None known | UNVERIFIED | Singer, Bremer & Stuckey (1972); NMC v7 documentation | UNVERIFIED |
| Correlates of War: MIDLOC 2.1 (militarized interstate dispute locations) | `raw/cow/midloc_2_1/*` | https://correlatesofwar.org/data-sets/mids/ | UNVERIFIED (as above) | UNVERIFIED | None known | UNVERIFIED | Braithwaite (2010) / MIDLOC 2.1 codebook (supplied) | UNVERIFIED |
| UCDP GED v26.1, UCDP/PRIO ACD, One-sided, Actor, Organized Violence CY (v26.1) | `raw/ucdp/*.zip` | https://ucdp.uu.se/downloads/ | Expected: CC BY 4.0 | UNVERIFIED (expected yes) | UNVERIFIED (none expected) | UNVERIFIED (expected: free with attribution) | Expected: Sundberg & Melander (2013) *JPR* 50(4), and Davies et al. (2026) UCDP annual article; cite the version (v26.1) | UNVERIFIED |
| VIEWS `fatalities003` forecasts, run 2026-09 (cm and pgm) | `raw/views/*.csv` | https://viewsforecasting.org/ (data API / downloads) | UNVERIFIED (expected CC BY 4.0) | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: Hegre et al., VIEWS; cite model `fatalities003` and run | UNVERIFIED |
| WFP market food prices (global, 2025 and 2026 files) | `raw/wfp_prices/*.csv` | https://data.humdata.org/ (WFP food prices) | UNVERIFIED (HDX WFP price data is usually CC BY-IGO) | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: "World Food Programme (WFP), VAM / DataViz, via HDX" | UNVERIFIED |
| FEWS NET IPC-compatible classifications (CS, ML1, ML2 KML/GeoJSON) | `raw/fewsnet/*` | https://fews.net/data | UNVERIFIED (FEWS NET data are generally published for free reuse) | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: "FEWS NET"; maps also note "© Mapbox © OpenStreetMap" for the basemap (not part of the data) | UNVERIFIED |
| IOM DTM Somalia Emergency Trend Tracking (since June 2026, week 19) | `raw/iom_dtm/IOM_DTM_ETT_SOM_*.xlsx` | https://dtm.iom.int/somalia | UNVERIFIED (IOM DTM terms of use apply; often CC BY-IGO on HDX) | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: "IOM Displacement Tracking Matrix (DTM)"; the workbook has a "Contacts & Partners" sheet that may list partners to credit | UNVERIFIED |
| IOM DTM South Sudan Event Tracking (Jan–Sep/Oct 2026) | `raw/iom_dtm/IOM_DTM_SSD_*.xlsx` | https://dtm.iom.int/south-sudan | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | Expected: "IOM DTM South Sudan" | UNVERIFIED |
| Kenya Internal Displacement Demographic Dataset 2024–2025 | `raw/iom_dtm/Kenya_Displacement_*.xlsx` | **Unknown.** The workbook does not name its publisher | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | **Publisher unknown, please supply** |
| ACLED (events 2024-11-09 to 2025-11-07, exported 2026-11-14) | `restricted/acled/*.csv` | https://acleddata.com/ | Brief says written permission exists, but **it was not supplied**. Until it is, the standard ACLED Terms of Use and EULA apply (§2.2). | Standard terms: restricted | Standard terms: derived outputs must not allow events to be reconstructed | Standard terms: no redistribution of raw or event-level data to third parties | "Armed Conflict Location & Event Data (ACLED); www.acleddata.com" | **BLOCKED: permission document missing** |
| WHO GHoA SRP Aug 2026–Jan 2027 | `docs/reference/ghoa-...pdf` | WHO | Reference document, not a dataset | n/a | n/a | n/a | Cite WHO SRP | Reference only |
| FEWS NET map images (10 WebP) | `docs/reference/fewsnet_maps/` | FEWS NET | Reference images. They duplicate the KML/GeoJSON data and are not used as data. | n/a | n/a | n/a | "Source: FEWS NET" | Reference only |

## ACLED permission scope

**Not yet determined.** `docs/reference/ACLED_permission.*` was not supplied. Until it arrives:

- `acled_display` will be set to `none` in config. The standard-terms default is aggregated, transformed output only, and the brief limits the setting to what the written permission *clearly* allows.
- The ACLED file stays in `data/restricted/` (gitignored). It may be used internally for model features and the with/without-ACLED comparison, with aggregated, non-reconstructable outputs only.
- Once the permission arrives I will fill in: permitted uses; permitted displays; whether event-level data may be shown to authenticated users; restrictions on sharing and exports; required attribution.

## Observations relevant to licensing

- The ACLED export ends 2025-11-07, exactly 12 months (minus a week) before its 2026-11-14 export date. That matches the 12-month recency lag on ACLED's free and research access tiers. If the written permission grants a different tier, the data may need re-exporting.

<!-- AUTO:FETCHED START (generated by aidtrace_pipeline.fetch; do not edit by hand) -->

## Fetched automatically from the approved allowlist

| Source | Versions promoted | Latest version | Last accessed | Example URL | Licence (allowlist / provider) | Attribution |
|---|---|---|---|---|---|---|
| CHIRPS rainfall | 430 | 202610 | 2026-11-15 | https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/africa/tifs/chirps-v3.0.2 | CC BY 4.0 (CHC) | Funk et al. (2015) CHIRPS, Climate Hazards Center, UC Santa Barbara |
| FEWS NET | 101 | SS_202606 | 2026-11-15 | https://fdw.fews.net/api/ipcpackage/ | FEWS NET data (public; terms UNVERIFIED) |  |
| UCDP GED + monthly Candidate | 1 | candidate_v26_01_26_09 | 2026-11-15 | https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_01_26_09.csv | CC BY 4.0 | Uppsala Conflict Data Program (UCDP); Sundberg & Melander 2013; Hegre et al. (Candidate) |
| UNHCR PRMN (Somalia) | 1 | 2025-04-28 | 2026-11-15 | https://data.humdata.org/dataset/475e2e3c-3cec-4961-b73c-d8e68791ce60/resource/9 | CC BY (HDX metadata) |  |
| WFP food prices | 5 | DJI_2026-11-15 | 2026-11-15 | https://data.humdata.org/dataset/b9e4ffde-205c-4887-beb0-a1fa027e8bdc/resource/1 | CC BY-IGO (HDX metadata) / HDX: cc-by-igo |  |
| WorldPop | 3 | DJI_wpgp_2020 | 2026-11-15 | https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/DJI/dji_ppp_2020. | CC BY 4.0 (hub.worldpop.org/data/licence.txt) | WorldPop (www.worldpop.org), University of Southampton |

Full per-file provenance (URL, access time, version, size, SHA-256, check statistics): `data/provenance.jsonl`.
Sources needing keys (IPC, IOM DTM API, GloFAS/CDS, HDX HAPI, ReliefWeb) and ACLED (written permission) are not fetched until configured.

<!-- AUTO:FETCHED END -->
