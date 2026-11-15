# AidTrace (beta)

A humanitarian aid verification and diversion-risk platform for the Greater Horn of Africa. It is built as a pilot for the WHO GHoA food insecurity and health crisis response (SRP, August 2026 – January 2027). Somalia is the pilot country.

It has two parts:

1. **Verification.** A recipient confirms a consignment with an offline phone app: a signed QR code, a photo and a PIN. A second **safety (duress) PIN** looks identical on the phone but quietly alerts the organisation's security focal point.
2. **Risk prioritisation.** A district-level map of humanitarian need and of the risk of violence. It changes **how** aid is delivered (extra verification, low-profile logistics), never **whether**.

> **Status:** all 8 milestones are complete. The pipeline covers all six SRP countries. **The beta app serves five together: Djibouti, Kenya, Somalia, South Sudan and Sudan** (`AIDTRACE_COUNTRIES`). WorldPop population (KEN, SSD), WorldPop buildings and urban extents (SOM), and Correlates of War NMC and MIDLOC were added on 2026-11-15. Read `DECISIONS.md` for open items and `docs/THREAT_MODEL.md` (*Before live deployment*) for what must be done before handling real data.

---

## Quick start: the demo (synthetic data only)

You need Python 3.11 and Node 20+ (see *Setup* below).

```powershell
$py = "$env:USERPROFILE\.venvs\aidtrace\Scripts\python.exe"
cd aidtrace
& $py -m pip install --no-build-isolation -e "pipeline[test]" -e "server[test]"
cd web; npm install; npm run build; cd ..
cd recipient; npm install; npm run build; cd ..
$env:AIDTRACE_MODE = "demo"
& "$env:USERPROFILE\.venvs\aidtrace\Scripts\aidtrace-server.exe" serve --port 8000
```

Open **http://localhost:8000**. The landing page explains the platform and has a role switcher:

- **Field staff, security focal point, HQ admin:** open the distributor app with one click (no password; demo only).
- **Recipient (demo phone):** opens `/recipient/`. Tap **"Set up a demo phone"**. The demo PINs are **normal 2468** and **safety 1357**.

To follow one consignment end to end:
1. Set up the demo phone.
2. As field staff, create a shipment to the demo clinic's district.
3. Open the shipment and copy (or scan) its QR code into the phone.
4. Confirm with the normal PIN, the safety PIN or a wrong PIN.
5. Back in the distributor app, field staff see a verified delivery for either correct PIN. The security focal point sees the duress alert. A wrong PIN shows as "unverified, PIN mismatch".

The first `serve` in demo mode seeds synthetic data automatically. Reset it with `aidtrace-server demo-reset`. Demo data lives in `~/.aidtrace/demo/`, and the demo uses its own keys. Demo routes do not exist in live mode, and the demo never shows ACLED-derived content.

---

## Repository layout

```
data/raw/            datasets as supplied (never modified)       → data/LICENCES.md, docs/DATA_INVENTORY.md
data/restricted/     ACLED (gitignored; standard-terms use only)
data/processed/      panel, model outputs, risk scores, need index (generated; gitignored)
pipeline/            Python: loaders, panel, model, need index, triggers
server/              FastAPI: auth, shipments, signed QR, sealed receipts, SMS, risk API, demo
web/                 Distributor app: React, Vite, MapLibre, deck.gl
recipient/           Recipient PWA: React, libsodium, html5-qrcode, IndexedDB, service worker
docs/                inventory, panel, model, triggers, protocol, reconciliation, threat model
DECISIONS.md         every design and policy decision, open items flagged
```

## Setup (Windows; as used on the development machine)

```powershell
winget install --id Python.Python.3.11 -e --source winget --scope user
winget install --id OpenJS.NodeJS.LTS -e --source winget --scope user
python -m venv $env:USERPROFILE\.venvs\aidtrace
$py = "$env:USERPROFILE\.venvs\aidtrace\Scripts\python.exe"
& $py -m pip install --use-feature=truststore --upgrade pip setuptools wheel
& $py -m pip install --no-build-isolation -e "pipeline[test]" -e "server[test]"
$env:NODE_OPTIONS = "--use-system-ca"
```

Known constraints on this machine (see `DECISIONS.md`):
- **HTTPS inspection:** pip uses the Windows certificate store (`truststore`), and npm uses `--use-system-ca`. Never disable TLS verification.
- **Windows Application Control** blocks some newer compiled wheels. pandas is pinned to 2.2.3 and pyogrio to 0.10.0. LightGBM loads the MSVC runtime bundled with scikit-learn.
- **Path length:** keep the virtual environment on a short path (`%USERPROFILE%\.venvs\aidtrace`).

## Data pipeline

```powershell
foreach ($c in "SOM","ETH","KEN","SSD","SDN","DJI") {
  & $py -I -m aidtrace_pipeline.panel --country $c    # admin-2 x month panel  -> docs/PANEL.md
  & $py -I -m aidtrace_pipeline.need  --country $c    # need index + SRP triggers -> docs/TRIGGERS.md
  & $py -I -m aidtrace_pipeline.model --country $c    # temporal CV, ACLED comparison, scores -> docs/model_results_<iso>.md
}
& $py -I -m aidtrace_pipeline.pooled --score          # risk scores for all six countries from the pooled model,
                                                      # plus pooled vs per-country evaluation -> docs/model_results_pooled.md
```

- Each loader has a `refresh()` that rebuilds only when its inputs have changed. Add `--force` to rebuild everything.
- `USE_ACLED=false` (or `--no-acled`) runs without ACLED.
- **Scheduling:** run `update --fetch` (below) monthly, for example with Windows Task Scheduler or cron. `build_report_<iso>.json` and the run manifest record what changed.

### Automatic data updates (approved sources only)

```powershell
& $py -I -m aidtrace_pipeline.fetch sources          # allowlist: which sources are auto / need a key / manual
& $py -I -m aidtrace_pipeline.fetch run              # fetch new releases into data/holding/, check, promote
& $py -I -m aidtrace_pipeline.fetch review           # anything HELD (sudden jump, dropped coverage, bad values)
& $py -I -m aidtrace_pipeline.fetch approve SOURCE VERSION FILE --note "reviewer, reason"
& $py -I -m aidtrace_pipeline.update [--fetch]       # incremental rebuild of changed sources, need, pooled scores
```

- Only hosts listed in `pipeline/aidtrace_pipeline/fetch/allowlist.yaml` are contacted, through official APIs or download pages. There is no web search, and mirrors (Kaggle, GitHub, personal sites) are refused. Each redirect hop is re-checked.
- Downloads are untrusted data: they are parsed, never executed. Executable file types are refused.
- Every download is logged in `data/provenance.jsonl`, and `data/LICENCES.md` gets an auto-generated block.
- Promoted files go to `data/raw/fetched_<source>/<version>/` and are never overwritten. Each `update` run writes `data/processed/model/run_manifest_<UTC>.json`, listing every data version used.
- Keys are read from environment variables: `IPC_API_KEY`, `DTM_API_KEY`, `CDSAPI_KEY`, `HDX_HAPI_APP_IDENTIFIER`, `RELIEFWEB_APPNAME`. ACLED (`ACLED_API_KEY`) stays off without written permission.
- UNHCR PRMN is used only after a person reviews `data/crosswalks/prmn_som_districts.csv`: set `reviewed=yes` on each row checked.

## Live server

```powershell
$cli = "$env:USERPROFILE\.venvs\aidtrace\Scripts\aidtrace-server.exe"
& $cli setup-keys                       # Ed25519 signing, X25519 box and SMS master keys -> ~/.aidtrace/keys/live
& $cli create-user admin hq_admin       # prompts for a password (12+ characters)
& $cli serve --port 8000                # put TLS in front (reverse proxy or uvicorn --ssl-*)
& $cli retention                        # daily: purge photos and free text older than 180 days
& $cli rotate-keys                      # key rotation (old keys keep verifying and decrypting until retired)
```

| Environment variable | Purpose |
|---|---|
| `AIDTRACE_MODE` | `live` (default) or `demo` |
| `AIDTRACE_COUNTRIES` | countries served together (default `DJI,SOM,SDN,SSD,KEN`; add `ETH` to include Ethiopia) |
| `AIDTRACE_DATA_DIR`, `AIDTRACE_KEY_DIR` | data and key locations (default `~/.aidtrace/...`; never inside the repo) |
| `AIDTRACE_SESSION_IDLE_MIN`, `AIDTRACE_SESSION_MAX_HOURS` | session timeouts (30 min / 12 h) |
| `AIDTRACE_RETENTION_DAYS` | retention period (180) |
| `AIDTRACE_SUBMIT_MIN_MS` | minimum response time for recipient submissions (800 ms; timing protection) |
| `AIDTRACE_DURESS_WEBHOOK`, `AIDTRACE_DURESS_EMAIL`, `AIDTRACE_SMTP_*` | duress notification channels (organisation-owned) |
| `AIDTRACE_SMS_GATEWAY_SECRET` | shared secret for the inbound SMS webhook (required outside demo) |
| `AIDTRACE_CORS_ORIGINS` | only if the web app is served from another origin |

Non-secret configuration lives in two files:
- `server/aidtrace_server/server.yaml`: catalogue, roles, verification rates, reconciliation ratios, PSEAH contacts, SMS number.
- `pipeline/aidtrace_pipeline/pipeline.yaml`: panel, model, need weights, trigger thresholds, `acled_display`.

## Development

```powershell
cd web;       npm run dev      # :5173, proxies /api to :8000
cd recipient; npm run dev      # :5174 (service worker only in production builds)
```

## Tests

```powershell
& $py -I -m pytest pipeline/tests server/tests -q    # 58 Python tests: loaders, model, server, SMS, demo end to end
cd recipient; npm test                                # 10 protocol tests against the shared Python vectors
cd web; npx tsc -b                                    # type-check
```

After any protocol change, regenerate the cross-language vectors from `server/`: `python -I -m tests.vectors`.

## Documentation

| File | Contents |
|---|---|
| `docs/DATA_INVENTORY.md`, `data/LICENCES.md` | what data exists, gaps, licences (most UNVERIFIED) |
| `docs/PANEL.md` | panel data dictionary and missing-data rules |
| `docs/MODEL_REPORT.md` | model results and where the model fails |
| `docs/TRIGGERS.md` | SRP §7 triggers and thresholds (to confirm) |
| `docs/PROTOCOL.md` | QR, enrolment, sealed receipts, SMS codes, PWA behaviour |
| `docs/RECONCILIATION.md` | consumption checks (provisional ratios) |
| `docs/THREAT_MODEL.md` | threats, mitigations, residual risk, pre-live checklist |

## Data attribution

UCDP GED v26.1 (Uppsala University) · FEWS NET · WFP market prices (via HDX) · IOM DTM · OCHA COD-AB (via HDX, CC BY-IGO) · VIEWS (PRIO/Uppsala) · WorldPop (University of Southampton; buildings CC BY-SA 4.0, footprints Ecopia/Maxar) · Correlates of War (NMC v7, MIDLOC 2.1) · basemap OpenFreeMap / © OpenStreetMap contributors. ACLED (www.acleddata.com) is used internally under its standard terms and is not displayed. See `data/LICENCES.md`.
