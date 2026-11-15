# Operational triggers (SRP §7)

The triggers are evaluated monthly per district by `python -I -m aidtrace_pipeline.need --country SOM`. The access trigger is evaluated live by the server from field reports. Thresholds live in config (`pipeline/aidtrace_pipeline/pipeline.yaml` → `triggers`, and `server/aidtrace_server/server.yaml` → `access_reports_90d`), not in code.

**All thresholds are starting points and need confirmation by the project owner.**

| Trigger (SRP §7) | Rule | Threshold | Source of threshold | Data | Status |
|---|---|---|---|---|---|
| Deterioration in IPC classification | Current max phase higher than the previous current classification within the lookback | lookback 24 months | Project choice | FEWS NET CS | Evaluated. No alerts yet: the two Somalia CS snapshots are 28 months apart. |
| Expansion of Phase 4–5 areas (current) | Current max phase ≥ 4 | ≥ 4 | IPC Technical Manual 3.1: Phase 4 = Emergency | FEWS NET CS | Evaluated |
| Expansion of Phase 4–5 areas (projected) | Projected max phase ≥ 4 | ≥ 4 | Same | FEWS NET ML1/ML2 | Evaluated |
| GAM/SAM above emergency thresholds | GAM ≥ 15% or SAM ≥ 2% | 15% / 2% | WHO emergency threshold for GAM (≥ 15%); SAM 2% is a common cluster threshold (**confirm**) | IPC AMN / SMART | **Not evaluable: no data** |
| Outbreak alerts | Any confirmed cholera/measles/dengue/malaria alert in the district | any | WHO IDSR alert definitions | WHO AFRO/EMRO, EWARS | **Not evaluable: no data** |
| Rising crude or under-five mortality | CMR ≥ 1 or U5MR ≥ 2 per 10,000 per day | 1 / 2 | Sphere Handbook emergency thresholds | SMART mortality surveys | **Not evaluable: no data** |
| Deterioration in health facility functionality | Share of functional facilities falls ≥ 20 percentage points | 20 pp | Project choice (**confirm**) | WHO HeRAMS | **Not evaluable: no data** |
| Large-scale displacement | Monthly new arrivals ≥ 5,000, or conflict-driven arrivals ≥ 2,000 | 5,000 / 2,000 | Project choice (**confirm**). Not per-capita, because there is no population data yet. | IOM DTM ETT | Evaluated (2026-06 onwards, assessed districts only) |
| Significant deterioration in access | ≥ 3 `access_denied` field reports (not rejected) in 90 days | 3 | Project choice (**confirm**) | AidTrace field reports | Evaluated live by the server |
| New climate shocks | Rainfall anomaly z ≥ 2, or flood extent anomaly | z ≥ 2 | Project choice (**confirm**) | CHIRPS / GloFAS | **Not evaluable: no data** |

Every alert stores its district, month, trigger, value, threshold, a one-line evidence string and the data source. A rule without data is reported as **not evaluable**, never as "no alert". The risk API returns this status with every response (`trigger_status`).

Current run (all unit-months in each panel):

| Country | Alerts | Current Phase 4–5 | Projected Phase 4–5 | Large displacement |
|---|---|---|---|---|
| Somalia | 89 | 39 | 41 | 9 |
| South Sudan | 207 | 25 | 129 | 53 |
| Sudan | 252 | n/a (no current classification) | 252 | n/a (no DTM) |
| Ethiopia | 16 | 8 | 8 | n/a |
| Kenya | 0 | 0 | 0 | n/a |
| Djibouti | 0 | n/a (no FEWS NET) | n/a | n/a |

Kenya's 2026 classifications reach Phase 3 at most at district level. Its Phase 4 appears only at an IDP-camp point, which does not set a district phase. For Djibouti, no FEWS NET or DTM data was supplied, so its zero alerts mean nothing could be checked, not that the situation is calm.
