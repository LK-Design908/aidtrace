# Model report: diversion/violence risk, Somalia pilot (Milestone 3)

2026-11-14. All numbers come from [`model_results_som.md`](model_results_som.md), which this command regenerates:

```powershell
& $env:USERPROFILE\.venvs\aidtrace\Scripts\python.exe -I -m aidtrace_pipeline.model --country SOM
```

Add `--no-acled` (or set `USE_ACLED=false`) to run without ACLED. Both modes are tested.

## What is predicted

The probability that a district (74 modelling units; Banadir merged) has **at least one fatal UCDP GED event** in a target month, **1 and 3 months ahead**. This is a proxy for the security environment around a delivery, not a measure of diversion itself. No diversion data exists yet. Field reports and diversion proxies will be shown as a separate layer and kept out of the model until there are enough of them (brief §5.2).

Scores change **how** aid is delivered (verification, protection, modality), never **whether** it is delivered.

## Method

- **Features:** recorded conflict history up to the forecast origin only:
  - lags of 1, 3, 6 and 12 months;
  - rolling 3/6/12/36-month rates and fatalities;
  - months since the last fatal event;
  - counts by violence type;
  - events located only to region or country level;
  - spatial lags from neighbouring districts;
  - district area and target-month seasonality.

  Need inputs are also included: IPC current phase carried forward at most 12 months, IPC projections only if issued by the origin, and price anomalies. They are almost entirely missing before 2024, so they contribute little.
- **Models:**
  - (a) persistence: last month's value;
  - (b) logistic regression on five conflict-history features;
  - (c) LightGBM on all features, with fixed, untuned hyperparameters (`pipeline.yaml`), so nothing is tuned on test data.
- **Validation:** expanding window. For each test year 2021–2025, models train only on rows whose target month falls before that year (2017 onwards). That gives 5 folds × 12 months × 74 units = 4,440 test rows per horizon.
- **Leakage guards:** these are tested in `pipeline/tests/test_model.py`.
  - Scrambling all data after an origin leaves that origin's features unchanged.
  - Every training target precedes every test target.
  - IPC projections are never used before they were issued.

## Results (pooled 2021–2025, without ACLED)

| Horizon | Model | AUC [95% CI] | Brier | Precision / recall at 20% alert rate |
|---|---|---|---|---|
| 1 month | Persistence | 0.685 [0.637, 0.733] | 0.144 | 0.45 / 0.45 (flags 13%) |
| 1 month | Logistic | 0.844 [0.806, 0.881] | 0.088 | 0.40 / 0.61 |
| 1 month | LightGBM | 0.844 [0.803, 0.882] | 0.089 | 0.40 / 0.60 |
| 3 months | Persistence | 0.631 [0.577, 0.693] | 0.168 | 0.36 / 0.36 (flags 13%) |
| 3 months | Logistic | 0.826 [0.777, 0.870] | 0.091 | 0.37 / 0.56 |
| 3 months | LightGBM | 0.827 [0.780, 0.870] | 0.093 | 0.37 / 0.57 |

Base rate: 13.2% of district-months have a fatal event. Results are stable across years: LightGBM AUC is 0.83–0.87 at 1 month and 0.81–0.84 at 3 months in every fold.

### The honest reading

1. **Both learned models clearly beat persistence.** The AUC gain is about 0.16–0.19, and the Brier score is about 40% lower.
2. **LightGBM does not beat the five-variable logistic regression.** The two are indistinguishable on every metric, with overlapping CIs. The extra features (need, prices, finer conflict lags) add nothing measurable with current data. **Recommendation:** treat the logistic model as the reference. Keep LightGBM (which produces the scores below) only because the brief specifies it and it can absorb need and field-report features later. Either choice gives the same ranking quality today.
3. **The model mostly learns where conflict has been.** The 36-month rate of fatal months alone accounts for about 37% of LightGBM's gain, and state-based events over 12 months for another 12%. That is useful for routing, but it is not foresight.
4. **New outbreaks are the weak spot.** In districts with no fatal event in the previous 12 months (about half of all rows, base rate 2.6–3.3%):
   - AUC is 0.77–0.78, so the model still ranks them better than chance.
   - But at a 20% alert rate only **6–9% of flags are correct**, and Brier is no better than a constant base-rate forecast (0.025 vs 0.026).
   - In practice the model will not reliably warn of violence spreading into currently quiet districts.
5. **Calibration** is good below about 0.4. Above that, LightGBM is **over-confident**: a predicted 0.45–0.55 corresponds to roughly 0.36–0.37 observed (see `model/calibration_som_h1.png`). Bands above "high" should be read cautiously. Recalibration, for example isotonic regression on the out-of-fold predictions, is a reasonable next step.
6. **Outcome measurement:** GED records *reported* fatal events. Under-reporting in remote, al-Shabaab-controlled areas means a "0" can hide violence. The model inherits that bias: it will under-rate places where reporting is worst.

## With vs without ACLED

ACLED data: Armed Conflict Location & Event Data (ACLED), www.acleddata.com. It is used under the standard ACLED terms (see `DECISIONS.md`). Only aggregated statistics appear here.

Coverage is only 11 complete months (2024-12 to 2025-10). The comparison therefore uses one fold per target month over 2025-03 to 2025-12, with ACLED features present only in the last months of each training set.

| Horizon | LightGBM AUC without | with ACLED | ΔAUC [95% CI] |
|---|---|---|---|
| 1 month | 0.879 | 0.879 | −0.000 [−0.008, 0.010] |
| 3 months | 0.859 | 0.854 | −0.005 [−0.016, 0.006] |

**ACLED adds no measurable skill in this test, but the test has almost no power.** The model sees ACLED values in fewer than 10% of its training rows. This says nothing about ACLED's value with a full history; a multi-year export would be needed to answer that. The secondary model (any ACLED event, h=1, 6 test months) reached AUC 0.78 against 0.70 for persistence, which is again too short to rely on.

## VIEWS benchmark

**Not done.** The only VIEWS run supplied (fatalities003, issued 2026-09) forecasts 2026-10 onwards, and no UCDP outcomes exist for those months yet. A benchmark needs archived VIEWS runs whose forecasts fall in 2021–2025. `views.py` already maps PRIO-GRID cells to districts, so adding them is straightforward. VIEWS predicts fatality counts, not "≥1 fatal event", so the comparison would use the ranking (AUC) of `views_pred_fatalities` against our probabilities.

## Current scores and their limits

- Output: `data/processed/risk/<iso>_risk.parquet` for all six countries. Since 2026-11-15 the scores come from the **pooled six-country** bagged LightGBM (20 members, no ACLED, country indicators; `pipeline.yaml` → `model.scoring: pooled`), with a probability, a 5–95% interval and a band per unit and horizon. Set `scoring: per_country` to go back to per-country models.
- **The forecast origin is 2025-12**, the last month of UCDP data, **11 months before today**. The "1-month" forecast is for 2026-01. **These scores are not current.** Each row carries `stale_months` and must be displayed with its data date (brief §5.3). Adding the GED Candidate monthly releases for 2026 would bring the origin up to about one month ago.
- **Intervals** reflect model uncertainty only (variation across bootstrap resamples), not uncertainty in the data or the outcome.
- **Bands** (`pipeline.yaml`: low < 0.10 ≤ elevated < 0.30 ≤ high < 0.60 ≤ severe) are **provisional and need owner sign-off.** At origin 2025-12, the pooled model places 44 / 16 / 10 / 4 Somali units in low / elevated / high / severe at 1 month (the per-country model gave 46 / 15 / 9 / 4).

## Where the model fails (summary)

- It cannot see 2026: there is no conflict data after 2025-12.
- It is weak on onsets in quiet districts.
- It is over-confident at high probabilities.
- It is blind to under-reported violence.
- It measures violence, not diversion.
- It gains nothing yet from need, price or displacement data, because those sources only start in 2024–2026.

None of these weaken the delivery-modality use: high-need districts with recent violence are reliably identified. But the scores must not be presented as predictions of specific incidents.

## Milestone 8: all six SRP countries

Per-country results are in `docs/model_results_<iso>.md`, and the pooled comparison is in `docs/model_results_pooled.md`. The method, features and temporal folds are the same as for Somalia. In the table, AUC is pooled over 2021–2025 and "onset" means districts with no fatal event in the previous 12 months.

| Country | Districts | Base rate | Persistence | Logistic | LightGBM | **Pooled LightGBM** | Onset AUC (LightGBM) |
|---|---|---|---|---|---|---|---|
| Ethiopia | 107 zones | 11.9% | 0.725 | 0.880 | 0.865 | **0.889** | 0.64 |
| Kenya | 290 | 0.6% | 0.585 | 0.951 | 0.954 | **0.959** | 0.92 |
| Somalia | 74 | 13.2% | 0.685 | 0.844 | 0.844 | **0.852** | 0.77 |
| Sudan | 189 | 5.8% | 0.696 | 0.857 | 0.853 | **0.871** | 0.70 |
| South Sudan | 79 | 2.7% | 0.556 | 0.702 | 0.669 | **0.706** | 0.54 |
| Djibouti | 20 | 0.1% | n/a | n/a | n/a | n/a | n/a |

The table is for the 1-month horizon; 3-month results are slightly lower everywhere (see each country's results file).

**Reading it:**
- **A pooled six-country model is best in every country** (+0.01 to +0.04 AUC over per-country LightGBM). It is never worse, and it gives the small-sample countries more training data. **Scoring now uses the pooled model** (switched 2026-11-15 at the owner's request). Pooled 1-month bands (low / elevated / high / severe): Somalia 44/16/10/4, Ethiopia 74/9/16/8, Sudan 137/35/14/3, South Sudan 76/3/0/0, Kenya 287/3/0/0, Djibouti 20/0/0/0 (flagged as not evaluable).
- **Kenya's high AUC (0.95) is easy ranking, not foresight.** Fatal events cover 0.6% of district-months and cluster in a few border counties (Mandera, Lamu, Garissa). Most districts are reliably quiet, so ranking is easy, but at any alert rate most alerts will still be false. Use precision, not AUC, to judge Kenya.
- **South Sudan is the weakest (≈0.70).** Recorded fatal events are rare (2.7%), localised violence is under-reported in GED, and onsets are barely better than chance (0.54). Treat South Sudan scores as low-confidence.
- **Djibouti cannot be evaluated.** There is one fatal district-month in 2021–2025 (61 events since 1989). Its scores are essentially the base rate. Do not show Djibouti risk bands as meaningful; use need, field reports and verification instead.
- **The logistic model again matches or beats per-country LightGBM** in four of five evaluable countries. Simple history explains most of the signal everywhere.
- **The ACLED comparison** (11 months) shows no measurable gain in any country, with the same power caveat as for Somalia.


## New data (2026-11-15): WorldPop, Correlates of War

The pooled model gained these features:
- log population, population density and under-5 share (Kenya, South Sudan);
- log building count and urban share (Somalia);
- historical interstate-dispute incidents (MIDLOC 1992–2010, all countries);
- national capabilities from the year before the forecast origin (COW NMC: military spending, personnel and CINC; all countries, to 2022).

Pooled LightGBM AUC at 1 month, before → after: Ethiopia 0.889 → 0.888, Kenya 0.959 → 0.957, Sudan 0.871 → 0.864, Somalia 0.852 → 0.851, South Sudan 0.706 → 0.710.

**The new data does not improve violence forecasting.** The changes are within ±0.007, which is noise across five folds. That is expected:
- The static attributes don't change over time, and conflict history already captures where violence happens.
- NMC is national and annual, and ends in 2022.
- MIDLOC records interstate disputes before 2011, not local violence.

Their value is on the **need** side: IPC is now weighted by people where population or buildings exist, and displacement is scored per capita. The features stay in the model because they are harmless to it and could matter with richer outcomes, but don't read their presence as added predictive skill.

## Next steps that would most improve it

1. UCDP GED Candidate 2026 monthly files, to make the scores current.
2. A multi-year ACLED export, for a real with/without comparison.
3. Archived VIEWS runs covering 2021–2025, for the benchmark.
4. Recalibration of the top probability range.
5. Once enough field reports exist, a diversion-specific outcome.

## Update 2026-11-15: data from the approved-source fetcher

New inputs (all promoted through `aidtrace_pipeline.fetch`; versions in `data/processed/model/run_manifest_20261115T182736Z.json`):

- UCDP Candidate v26_01_26_09. **Forecast origin moves from 2025-12 to 2026-09.**
- FEWS NET IPC packages 2017–2026: IPC history from 2017 instead of 2024. Feb and Jun 2025 are absent (FEWS NET service outage).
- WFP price histories from 1995+.
- WorldPop 2020 totals (SOM, SDN, DJI): IPC Phase 3+ is now people-weighted in five of the six countries.
- CHIRPS v3 rainfall 1991–2026-10: the `rain_z` and `rain3_z` features, the need component and the climate-shock trigger.

Pooled LightGBM CV AUC, before → after (h=1): ETH .888→.886, KEN .957→.955, SDN .864→.868, SOM .851→.850, SSD .710→.705. Changes are within ±0.006 everywhere, which is noise. **The new data do not measurably improve conflict forecasting**, as with the static data earlier. Their value is fresher scores and better need and trigger information, not a better conflict model. The per-country columns in `model_results_pooled.md` were not re-run.

South Sudan h=1 probabilities are 0.003–0.09, so no county reaches "elevated" (0.10). That is consistent with the low UCDP event rate there (0–8 events per month in 2025–26), and South Sudan stays flagged as low-confidence.

Rainfall trigger, a sanity check: Somalia's alerts cluster in the 2018 Gu floods, the 2019–2020 floods and the late-2023 El Niño floods (104 alerts). The 2021–22 drought produces few deficit alerts, because a 3-month z of −2 is hard to reach in arid districts with skewed rainfall. The threshold is provisional (owner to confirm). Kenya Feb–Mar 2026 shows a country-wide wet anomaly (median 3.8× normal) that **has not been independently verified**.
