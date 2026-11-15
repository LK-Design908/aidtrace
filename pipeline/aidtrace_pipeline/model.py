"""Diversion/violence risk model: evaluation and scoring.

    python -I -m aidtrace_pipeline.model --country SOM            # evaluate + score + report
    python -I -m aidtrace_pipeline.model --country SOM --no-acled # same with ACLED switched off

Outcome: P(>= 1 fatal UCDP GED event in the unit in target month), horizons 1 and 3 months.
Models: persistence (last month's value), logistic regression on lagged conflict history,
LightGBM on all features. Validation is strictly temporal: expanding window, each fold trains
only on rows whose target month precedes the test year.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

# LightGBM needs the MSVC OpenMP runtime (vcomp140.dll), which scikit-learn bundles.
_libs = os.path.join(os.path.dirname(sklearn.__file__), ".libs")
if os.name == "nt" and os.path.isdir(_libs):
    os.add_dll_directory(_libs)
import lightgbm as lgb  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import brier_score_loss, roc_auc_score  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from .common import PROCESSED, RESTRICTED_OUT, cfg, log, path  # noqa: E402
from .features import LOGIT_FEATURES, add_target, build_features, feature_columns  # noqa: E402

MC = lambda: cfg()["model"]  # noqa: E731


# ------------------------------------------------------------------ models

def fit_predict(model: str, train: pd.DataFrame, test: pd.DataFrame, feats: list[str], seed: int = 0) -> np.ndarray:
    if model == "persistence":
        return test["fatal_lag1"].to_numpy(dtype=float)
    if train["y"].nunique() < 2:          # e.g. Djibouti: no fatal events in the training window
        return np.full(len(test), float(train["y"].mean()) if len(train) else 0.0)
    if model == "logit":
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
        m.fit(train[LOGIT_FEATURES], train["y"])
        return m.predict_proba(test[LOGIT_FEATURES])[:, 1]
    if model == "lightgbm":
        m = lgb.LGBMClassifier(**MC()["lightgbm"], random_state=seed)
        m.fit(train[feats], train["y"])
        return m.predict_proba(test[feats])[:, 1]
    raise ValueError(model)


# ------------------------------------------------------------------ metrics

def prec_recall_at(y: np.ndarray, p: np.ndarray, rate: float) -> tuple[float, float, float]:
    """Flag the top `rate` share of rows (ties at the threshold included)."""
    thr = np.quantile(p, 1 - rate)
    flag = p >= thr if len(np.unique(p)) > 2 else p >= 0.5
    tp = int((flag & (y == 1)).sum())
    prec = tp / flag.sum() if flag.sum() else np.nan
    rec = tp / (y == 1).sum() if (y == 1).sum() else np.nan
    return prec, rec, float(flag.mean())


def metrics(df: pd.DataFrame, rate: float) -> dict:
    y, p = df["y"].to_numpy(int), df["p"].to_numpy(float)
    auc = roc_auc_score(y, p) if 0 < y.mean() < 1 else np.nan
    if not (0 < y.mean() < 1):
        return {"n": len(y), "base_rate": y.mean(), "auc": np.nan, "brier": brier_score_loss(y, p),
                "precision": np.nan, "recall": np.nan, "flag_rate": np.nan}
    pr, rc, fr = prec_recall_at(y, p, rate)
    return {"n": len(y), "base_rate": y.mean(), "auc": auc, "brier": brier_score_loss(y, p),
            "precision": pr, "recall": rc, "flag_rate": fr}


def bootstrap_ci(df: pd.DataFrame, fn, reps: int, seed: int = 0) -> tuple[float, float]:
    """Unit-cluster bootstrap 95% CI."""
    rng = np.random.default_rng(seed)
    ids = df["unit_id"].unique()
    groups = {k: v for k, v in df.groupby("unit_id")}
    vals = []
    for _ in range(reps):
        s = pd.concat([groups[i] for i in rng.choice(ids, len(ids), replace=True)])
        if 0 < s["y"].mean() < 1:
            vals.append(fn(s))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else (np.nan, np.nan)


def within_month_auc(d: pd.DataFrame) -> float:
    """Mean AUC computed separately per target month: how well units are ranked against each other."""
    vals = [roc_auc_score(x["y"], x["p"]) for _, x in d.groupby("target_month") if x["y"].nunique() == 2]
    return float(np.mean(vals)) if vals else np.nan


def calibration_table(df: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    q = pd.cut(df["p"], np.linspace(0, 1, bins + 1), include_lowest=True)
    return df.groupby(q, observed=True).agg(n=("y", "size"), mean_pred=("p", "mean"), obs_rate=("y", "mean")).reset_index()


# ------------------------------------------------------------------ evaluation

@dataclass
class Dataset:
    frame: pd.DataFrame
    feats: list[str]


def dataset(iso3: str, horizon: int, use_acled: bool) -> Dataset:
    f = add_target(build_features(iso3, use_acled), iso3, horizon)
    f = f[f["origin"] >= pd.Timestamp(MC()["train_origin_start"] + "-01")]
    return Dataset(f, feature_columns(f))


def expanding_cv(ds: Dataset, models: list[str]) -> pd.DataFrame:
    known = ds.frame[ds.frame["y"].notna()]
    preds = []
    for yr in MC()["test_years"]:
        start, end = pd.Timestamp(f"{yr}-01-01"), pd.Timestamp(f"{yr}-12-01")
        train = known[known["target_month"] < start]
        test = known[(known["target_month"] >= start) & (known["target_month"] <= end)]
        if test.empty:
            continue
        for m in models:
            p = fit_predict(m, train, test, ds.feats)
            preds.append(test[["unit_id", "origin", "target_month", "y", "fatal_mean12"]].assign(model=m, fold=yr, p=p))
    return pd.concat(preds, ignore_index=True)


def rolling_monthly_cv(ds: Dataset, models: list[str], first: str, last: str) -> pd.DataFrame:
    """One fold per target month (used where coverage is short, e.g. ACLED)."""
    known = ds.frame[ds.frame["y"].notna()]
    preds = []
    for m_ in pd.period_range(first, last, freq="M").to_timestamp():
        train, test = known[known["target_month"] < m_], known[known["target_month"] == m_]
        if test.empty:
            continue
        for m in models:
            p = fit_predict(m, train, test, ds.feats)
            preds.append(test[["unit_id", "origin", "target_month", "y"]].assign(model=m, fold=f"{m_:%Y-%m}", p=p))
    return pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()


def summarise(preds: pd.DataFrame, label: str) -> pd.DataFrame:
    rate, reps = MC()["alert_rate"], MC()["bootstrap_reps"]
    rows = []
    for m, d in preds.groupby("model"):
        r = {"variant": label, "model": m, **metrics(d, rate)}
        r["auc_lo"], r["auc_hi"] = bootstrap_ci(d, lambda s: roc_auc_score(s["y"], s["p"]), reps)
        r["brier_lo"], r["brier_hi"] = bootstrap_ci(d, lambda s: brier_score_loss(s["y"], s["p"]), reps)
        rows.append(r)
    return pd.DataFrame(rows)


def paired_auc_diff(a: pd.DataFrame, b: pd.DataFrame, reps: int) -> tuple[float, float, float]:
    """AUC(b) - AUC(a) on the same rows, with a unit-cluster bootstrap CI."""
    m = a.merge(b, on=["unit_id", "origin", "target_month", "y"], suffixes=("_a", "_b"))
    diff = lambda s: roc_auc_score(s["y"], s["p_b"]) - roc_auc_score(s["y"], s["p_a"])
    lo, hi = bootstrap_ci(m, diff, reps)
    return float(diff(m)), lo, hi


# ------------------------------------------------------------------ scoring

def band(p: float) -> str:
    for b in MC()["bands"]:
        if p < b["max"]:
            return b["name"]
    return MC()["bands"][-1]["name"]


def score_latest(iso3: str, horizon: int, ds: Dataset, model_name: str = "lightgbm_bagged_no_acled") -> pd.DataFrame:
    """Bagged LightGBM trained on all known rows; scores the latest origin with conflict data."""
    known = ds.frame[ds.frame["y"].notna()]
    origin = ds.frame["origin"].max()
    x = ds.frame[ds.frame["origin"] == origin]
    k = MC()["ensemble_size"]
    P = np.column_stack([
        fit_predict("lightgbm", known.sample(frac=1.0, replace=True, random_state=s), x, ds.feats, seed=s)
        for s in range(k)])
    lo_q, hi_q = MC()["interval"]
    out = x[["unit_id", "origin"]].copy()
    out["horizon"] = horizon
    out["target_month"] = origin + pd.DateOffset(months=horizon)
    out["prob"] = P.mean(axis=1)
    out["prob_lo"] = np.quantile(P, lo_q, axis=1)
    out["prob_hi"] = np.quantile(P, hi_q, axis=1)
    out["band"] = out["prob"].map(band)
    out["data_last_month"] = origin
    out["stale_months"] = (pd.Timestamp(date.today()).to_period("M") - origin.to_period("M")).n
    out["model"] = model_name
    out["note"] = "Estimate. Interval = spread across bagged models (model uncertainty only)."
    return out


# ------------------------------------------------------------------ main

def run(iso3: str, use_acled: bool) -> dict:
    iso3 = iso3.upper()
    outdir = path(PROCESSED) / "model"
    rdir = path(RESTRICTED_OUT) / "model"
    outdir.mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    reps = MC()["bootstrap_reps"]
    summaries, calib, scores, per_fold, acled_cmp, importances = [], {}, [], [], [], {}

    for h in MC()["horizons"]:
        ds = dataset(iso3, h, use_acled=False)
        preds = expanding_cv(ds, ["persistence", "logit", "lightgbm"])
        preds.to_parquet(outdir / f"cv_predictions_{iso3.lower()}_h{h}.parquet", index=False)
        quiet = preds[preds["fatal_mean12"] == 0]   # no fatal event in the 12 months up to origin
        for subset, label in [(preds, f"h{h}"), (quiet, f"h{h} quiet-12m (onset)")]:
            if subset["y"].nunique() < 2:
                continue
            s = summarise(subset, label)
            s["horizon"] = h
            s["auc_within_month"] = s["model"].map(lambda m: within_month_auc(subset[subset["model"] == m]))
            summaries.append(s)
        for (m, fold), d in preds.groupby(["model", "fold"]):
            per_fold.append({"horizon": h, "model": m, "fold": fold, **metrics(d, MC()["alert_rate"])})
        calib[h] = {m: calibration_table(d) for m, d in preds.groupby("model") if m != "persistence"}

        known = ds.frame[ds.frame["y"].notna()]
        imp_model = lgb.LGBMClassifier(**MC()["lightgbm"], random_state=0).fit(known[ds.feats], known["y"])
        importances[h] = (pd.Series(imp_model.booster_.feature_importance("gain"), index=ds.feats)
                          .sort_values(ascending=False))
        if MC().get("scoring", "pooled") == "per_country":
            scores.append(score_latest(iso3, h, ds))

        if use_acled:
            try:
                dsa = dataset(iso3, h, use_acled=True)
                first, last = MC()["acled_test_months"]
                pa = rolling_monthly_cv(dsa, ["lightgbm"], first, last)
                pn = rolling_monthly_cv(ds, ["lightgbm", "logit"], first, last)
                pa.to_parquet(rdir / f"cv_predictions_acled_{iso3.lower()}_h{h}.parquet", index=False)
                sa, sn = summarise(pa, f"h{h} with ACLED"), summarise(pn, f"h{h} without ACLED")
                d, lo, hi = paired_auc_diff(pn[pn.model == "lightgbm"], pa, reps)
                acled_cmp.append({"horizon": h, "with": sa, "without": sn, "auc_diff": d, "auc_diff_lo": lo,
                                  "auc_diff_hi": hi, "n": len(pa)})
            except (ValueError, FileNotFoundError) as e:     # e.g. no events in the comparison window
                log.warning("ACLED comparison skipped for %s h%s: %s", iso3, h, e)

    risk_dir = path(PROCESSED) / "risk"
    risk_dir.mkdir(parents=True, exist_ok=True)
    risk_file = risk_dir / f"{iso3.lower()}_risk.parquet"
    if scores:                       # scoring: per_country
        risk = pd.concat(scores, ignore_index=True)
        risk.to_parquet(risk_file, index=False)
    else:                            # scoring: pooled -> written by `python -m aidtrace_pipeline.pooled --score`
        risk = pd.read_parquet(risk_file) if risk_file.exists() else pd.DataFrame()

    summary = pd.concat(summaries, ignore_index=True)
    summary.to_csv(outdir / f"cv_summary_{iso3.lower()}.csv", index=False)
    pd.DataFrame(per_fold).to_csv(outdir / f"cv_per_fold_{iso3.lower()}.csv", index=False)
    plots = calibration_plots(calib, iso3)
    try:
        secondary = secondary_acled_model(iso3) if use_acled else None
    except (ValueError, FileNotFoundError) as e:
        log.warning("secondary ACLED model skipped for %s: %s", iso3, e)
        secondary = None
    report = write_report(iso3, summary, pd.DataFrame(per_fold), calib, importances, risk, acled_cmp, plots, secondary)
    return {"summary": summary.to_dict("records"), "report": str(report)}


def secondary_acled_model(iso3: str) -> dict:
    """Secondary target: any ACLED event in target month (h=1), rolling-origin within ACLED coverage."""
    from .features import acled_features
    a = acled_features(iso3).rename(columns={"month": "origin"})
    a = a.sort_values(["unit_id", "origin"])
    a["y"] = a.groupby("unit_id")["acled_any_lag1"].shift(-1)
    a["target_month"] = a["origin"] + pd.DateOffset(months=1)
    feats = ["acled_events_lag1", "acled_any_lag1", "acled_events_sum3", "acled_vac_lag1",
             "acled_battles_lag1", "acled_remote_lag1", "acled_fatalities_l1"]
    known = a[a["y"].notna()]
    months = sorted(known["target_month"].unique())[4:]   # need >= 4 months of training data
    preds = []
    for m_ in months:
        tr, te = known[known["target_month"] < m_], known[known["target_month"] == m_]
        if tr["y"].nunique() < 2:
            continue
        pers = te["acled_any_lag1"].to_numpy(float)
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[feats], tr["y"])
        preds.append(te[["unit_id", "origin", "target_month", "y"]].assign(model="persistence", p=pers))
        preds.append(te[["unit_id", "origin", "target_month", "y"]].assign(model="logit", p=lr.predict_proba(te[feats])[:, 1]))
    if not preds:
        return {"note": "insufficient ACLED coverage"}
    p = pd.concat(preds)
    p.to_parquet(path(RESTRICTED_OUT) / "model" / f"cv_predictions_acled_secondary_{iso3.lower()}.parquet", index=False)
    return {"summary": summarise(p, "ACLED any-event h1"), "months": [f"{m:%Y-%m}" for m in months]}


def calibration_plots(calib: dict, iso3: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = path("docs") / "model"
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for h, tabs in calib.items():
        fig, ax = plt.subplots(figsize=(4.5, 4.5))
        ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--", label="perfect")
        for (m, t), c in zip(tabs.items(), ["#2a6fdb", "#d9822b"]):
            ax.plot(t["mean_pred"], t["obs_rate"], marker="o", color=c, label=m)
        ax.set_xlabel("Mean predicted probability")
        ax.set_ylabel("Observed share with fatal event")
        ax.set_title(f"{iso3}, {h}-month horizon (CV {MC()['test_years'][0]}–{MC()['test_years'][-1]})", fontsize=10)
        ax.set_xlim(0, 1), ax.set_ylim(0, 1)
        ax.legend(frameon=False, loc="upper left")
        fig.tight_layout()
        f = d / f"calibration_{iso3.lower()}_h{h}.png"
        fig.savefig(f, dpi=130)
        plt.close(fig)
        files.append(f"model/{f.name}")
    return files


# ------------------------------------------------------------------ report

def _fmt(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def write_report(iso3, summary, per_fold, calib, importances, risk, acled_cmp, plots, secondary) -> Path:
    L = []
    L.append(f"<!-- AUTO-GENERATED by `python -m aidtrace_pipeline.model --country {iso3}` on {date.today()}. "
             "Narrative sections are in docs/MODEL_REPORT.md; this file holds the numbers. -->")
    L.append(f"# Model results: {iso3} (generated {date.today()})\n")
    L.append("Outcome: at least one fatal UCDP GED event in the unit in the target month. "
             f"Expanding-window CV, test years {MC()['test_years'][0]}–{MC()['test_years'][-1]} by target month. "
             f"Precision/recall at an alert rate of {MC()['alert_rate']:.0%} (persistence flags all units with a fatal event last month). "
             f"95% CIs: unit-cluster bootstrap, {MC()['bootstrap_reps']} reps.\n")
    L.append("## Pooled CV metrics (without ACLED)\n")
    L.append("`quiet-12m (onset)` = rows where the unit had no fatal event in the 12 months up to the origin. "
             "`Within-month AUC` = mean AUC computed separately for each target month (cross-sectional ranking).\n")
    L.append("| Horizon | Subset | Model | n | Base rate | AUC [95% CI] | Within-month AUC | Brier [95% CI] | Precision | Recall | Flag rate |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in summary.sort_values(["horizon", "variant", "model"]).iterrows():
        sub = "quiet-12m" if "quiet" in r.variant else "all"
        L.append(f"| {r.horizon} | {sub} | {r.model} | {r.n} | {_fmt(r.base_rate)} | {_fmt(r.auc)} [{_fmt(r.auc_lo)}, {_fmt(r.auc_hi)}] | "
                 f"{_fmt(r.get('auc_within_month'))} | "
                 f"{_fmt(r.brier)} [{_fmt(r.brier_lo)}, {_fmt(r.brier_hi)}] | {_fmt(r.precision)} | {_fmt(r.recall)} | {_fmt(r.flag_rate)} |")
    L.append("\n## Per test year (AUC / Brier)\n")
    pf = per_fold.pivot_table(index=["horizon", "fold"], columns="model", values=["auc", "brier"])
    L.append("| Horizon | Year | " + " | ".join(f"{m} AUC" for m in pf["auc"].columns) + " | " +
             " | ".join(f"{m} Brier" for m in pf["brier"].columns) + " |")
    L.append("|---|---|" + "---|" * (2 * len(pf["auc"].columns)))
    for (h, fold), r in pf.iterrows():
        L.append(f"| {h} | {fold} | " + " | ".join(_fmt(r[("auc", m)]) for m in pf["auc"].columns) + " | " +
                 " | ".join(_fmt(r[("brier", m)]) for m in pf["brier"].columns) + " |")
    L.append("\n## Calibration\n")
    for f in plots:
        L.append(f"![calibration]({f})\n")
    for h, tabs in calib.items():
        t = tabs.get("lightgbm")
        if t is not None:
            L.append(f"LightGBM h={h} reliability: " + "; ".join(
                f"{r.mean_pred:.2f}→{r.obs_rate:.2f} (n={r.n})" for _, r in t.iterrows()) + "\n")
    L.append("## Top LightGBM features (gain, model trained on all known rows)\n")
    for h, imp in importances.items():
        tot = imp.sum()
        L.append(f"- h={h}: " + ", ".join(f"{k} ({v / tot:.0%})" for k, v in imp.head(10).items()))
    L.append("\n## With vs without ACLED (rolling monthly origin within ACLED coverage)\n")
    if acled_cmp:
        L.append("| Horizon | Variant | Model | n | AUC [95% CI] | Brier |")
        L.append("|---|---|---|---|---|---|")
        for c in acled_cmp:
            for s in (c["without"], c["with"]):
                for _, r in s.iterrows():
                    L.append(f"| {c['horizon']} | {r.variant} | {r.model} | {r.n} | {_fmt(r.auc)} [{_fmt(r.auc_lo)}, {_fmt(r.auc_hi)}] | {_fmt(r.brier)} |")
            L.append(f"| {c['horizon']} | **ΔAUC (with − without, LightGBM)** | | {c['n']} | {_fmt(c['auc_diff'])} "
                     f"[{_fmt(c['auc_diff_lo'])}, {_fmt(c['auc_diff_hi'])}] | |")
    else:
        L.append("ACLED disabled for this run (`USE_ACLED=false`).")
    if secondary and "summary" in secondary:
        L.append("\n### Secondary model: any ACLED event, h=1\n")
        L.append(f"Test months: {', '.join(secondary['months'])}\n")
        L.append("| Model | n | Base rate | AUC [95% CI] | Brier |")
        L.append("|---|---|---|---|---|")
        for _, r in secondary["summary"].iterrows():
            L.append(f"| {r.model} | {r.n} | {_fmt(r.base_rate)} | {_fmt(r.auc)} [{_fmt(r.auc_lo)}, {_fmt(r.auc_hi)}] | {_fmt(r.brier)} |")
    L.append("\n## Latest scores\n")
    if risk.empty:
        L.append("No scores yet: scoring is pooled; run `python -m aidtrace_pipeline.pooled --score`.")
        risk = pd.DataFrame(columns=["origin", "horizon", "band", "target_month", "prob", "stale_months"])
    else:
        o = risk["origin"].max()
        L.append(f"Model: `{risk['model'].iloc[0]}`. Forecast origin {o:%Y-%m} (last month with UCDP data; "
                 f"{int(risk['stale_months'].iloc[0])} months before today).\n")
    L.append("| Horizon | Target | low | elevated | high | severe | Mean prob |")
    L.append("|---|---|---|---|---|---|---|")
    for h, d in risk.groupby("horizon"):
        vc = d["band"].value_counts()
        L.append(f"| {h} | {d['target_month'].iloc[0]:%Y-%m} | " + " | ".join(str(int(vc.get(b, 0))) for b in
                 ["low", "elevated", "high", "severe"]) + f" | {d['prob'].mean():.3f} |")
    L.append("\n## VIEWS benchmark\n")
    L.append("Not computed: the only VIEWS run supplied (fatalities003, 2026-09) forecasts 2026-10 onwards, and no "
             "UCDP outcomes exist for those months. A benchmark needs archived VIEWS runs covering 2021–2025.")
    out = path("docs") / f"model_results_{iso3.lower()}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="SOM")
    ap.add_argument("--no-acled", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    use_acled = cfg()["use_acled"] and not a.no_acled
    res = run(a.country, use_acled)
    log.info("report: %s", res["report"])
    print(pd.DataFrame(res["summary"])[["variant", "model", "n", "base_rate", "auc", "auc_lo", "auc_hi", "brier",
                                        "precision", "recall"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
