"""Train and evaluate late-delivery models on a time-based split.

    python src/train.py                         # uses data/features.csv
    python src/train.py --target-recall 0.6

Split    train < val_start <= val < test_start <= test  (by purchase date).
         Defaults put the cut points at the 70% / 85% quantiles of
         purchase dates; override with --val-start / --test-start.
Models   1) logistic regression baseline (class-weighted)
         2) gradient boosting: LightGBM if installed, otherwise
            scikit-learn HistGradientBoostingClassifier.
Metrics  PR-AUC (average precision) is the headline because late orders
         are the minority. The decision threshold is chosen on VALIDATION
         to reach --target-recall, then applied unchanged to TEST.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from features import CATEGORICAL_FEATURES, HISTORY_PAIRS, NUMERIC_FEATURES

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

ROOT = Path(__file__).resolve().parents[1]
SEED = 42
SMOOTHING = 20  # pseudo-orders for the seller/route late-rate prior

SKEWED = ["total_price", "total_freight", "max_item_price", "total_weight_kg", "max_weight_kg",
          "total_volume_l", "distance_km", "seller_prior_orders", "seller_prior_delivered",
          "seller_days_active", "route_prior_delivered", "route_recent_delivered"]


# --------------------------------------------------------------------- data
def time_split(df: pd.DataFrame, val_start: str | None, test_start: str | None):
    ts = df.purchase_ts
    vs = pd.Timestamp(val_start) if val_start else ts.quantile(0.70).normalize()
    te = pd.Timestamp(test_start) if test_start else ts.quantile(0.85).normalize()
    assert vs < te, "val_start must be before test_start"
    return df[ts < vs].copy(), df[(ts >= vs) & (ts < te)].copy(), df[ts >= te].copy(), vs, te


def add_history_rates(frames: list[pd.DataFrame], prior: float) -> list[str]:
    """Smoothed historical late rates; prior comes from the TRAIN split only."""
    for f in frames:
        for name, (late, n) in HISTORY_PAIRS.items():
            f[name] = (f[late] + SMOOTHING * prior) / (f[n] + SMOOTHING)
    return list(HISTORY_PAIRS)


# ------------------------------------------------------------------- models
def logistic_baseline(num_cols: list[str], cat_cols: list[str]) -> Pipeline:
    skewed = [c for c in num_cols if c in SKEWED]
    plain = [c for c in num_cols if c not in SKEWED]
    pre = ColumnTransformer([
        ("skewed", Pipeline([("imp", SimpleImputer(strategy="median")),
                             ("log", FunctionTransformer(lambda x: np.log1p(np.clip(x, 0, None)),
                                                         feature_names_out="one-to-one")),
                             ("sc", StandardScaler())]), skewed),
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), plain),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=50))]),
         cat_cols),
    ])
    return Pipeline([("pre", pre),
                     ("lr", LogisticRegression(max_iter=3000, class_weight="balanced", C=0.5))])


def as_category(frames: list[pd.DataFrame], cat_cols: list[str]) -> None:
    """Consistent category dtypes across splits (categories learned on train)."""
    for c in cat_cols:
        cats = frames[0][c].astype("category").cat.categories
        for f in frames:
            f[c] = pd.Categorical(f[c], categories=cats)


def fit_gbm(Xtr, ytr, Xva, yva):
    """Tune on validation. Returns (model fit on train, name, factory) where
    factory() builds an unfitted model with the chosen settings for refitting."""
    if lgb is not None:
        params = dict(learning_rate=0.03, num_leaves=31, min_child_samples=50, subsample=0.8,
                      subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=SEED, verbose=-1)
        model = lgb.LGBMClassifier(n_estimators=3000, **params)
        model.fit(Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="average_precision",
                  callbacks=[lgb.early_stopping(100, verbose=False)])
        n = max(50, model.best_iteration_ or 50)
        return model, f"LightGBM ({n} trees)", lambda: lgb.LGBMClassifier(n_estimators=n, **params)

    best = None
    for max_iter in (150, 300, 600):
        for lr in (0.05, 0.1):
            kw = dict(max_iter=max_iter, learning_rate=lr, max_leaf_nodes=31, min_samples_leaf=100,
                      l2_regularization=1.0, categorical_features="from_dtype", early_stopping=False,
                      random_state=SEED)
            m = HistGradientBoostingClassifier(**kw).fit(Xtr, ytr)
            ap = average_precision_score(yva, m.predict_proba(Xva)[:, 1])
            if best is None or ap > best[0]:
                best = (ap, m, f"HistGradientBoosting (iter={max_iter}, lr={lr})", kw)
    kw = best[3]
    return best[1], best[2], lambda: HistGradientBoostingClassifier(**kw)


# ------------------------------------------------------------------ metrics
def threshold_for_recall(y, p, target: float) -> float:
    """Highest threshold whose recall on (y, p) is still >= target."""
    prec, rec, thr = precision_recall_curve(y, p)
    ok = np.where(rec[:-1] >= target)[0]
    return float(thr[ok[-1]]) if len(ok) else float(thr[0])


def flag_rate_for_recall(y, p, target: float) -> float:
    """Share of orders to flag (riskiest first) so that recall on (y, p) >= target."""
    return float(np.mean(np.asarray(p) >= threshold_for_recall(y, p, target)))


def threshold_for_flag_rate(p, rate: float) -> float:
    """Score cut-off that flags the riskiest `rate` share of a batch (uses scores only, no labels)."""
    return float(np.quantile(p, 1 - rate))


def evaluate(y, p, thr: float) -> dict:
    pred = (p >= thr).astype(int)
    k = max(1, int(round(0.10 * len(p))))
    top = np.argsort(-p)[:k]
    return {
        "n": int(len(y)),
        "base_rate": round(float(np.mean(y)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "threshold": round(thr, 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred)), 4),
        "f1": round(float(f1_score(y, pred)), 4),
        "flagged_pct": round(float(pred.mean()), 4),
        "precision_at_top10pct": round(float(np.mean(np.asarray(y)[top])), 4),
        "recall_at_top10pct": round(float(np.asarray(y)[top].sum() / max(1, np.sum(y))), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", type=Path, default=ROOT / "data" / "features.csv")
    ap.add_argument("--val-start")
    ap.add_argument("--test-start")
    ap.add_argument("--target-recall", type=float, default=0.5)
    args = ap.parse_args()

    df = pd.read_csv(args.features, parse_dates=["purchase_ts"])
    tr, va, te, vs, ts = time_split(df, args.val_start, args.test_start)
    prior = float(tr.is_late.mean())
    rate_cols = add_history_rates([tr, va, te], prior)
    num_cols = NUMERIC_FEATURES + rate_cols
    cat_cols = CATEGORICAL_FEATURES
    feats = num_cols + cat_cols

    print(f"train  {tr.purchase_ts.min():%Y-%m-%d} .. {tr.purchase_ts.max():%Y-%m-%d}  n={len(tr):,}  late={tr.is_late.mean():.2%}")
    print(f"val    {va.purchase_ts.min():%Y-%m-%d} .. {va.purchase_ts.max():%Y-%m-%d}  n={len(va):,}  late={va.is_late.mean():.2%}")
    print(f"test   {te.purchase_ts.min():%Y-%m-%d} .. {te.purchase_ts.max():%Y-%m-%d}  n={len(te):,}  late={te.is_late.mean():.2%}\n")

    results, curves = {}, {}
    trva = pd.concat([tr, va])

    def run(name, label, fitted_on_train, make_unfitted):
        """Protocol: (1) model fit on train -> validation scores -> choose the
        flag rate that reaches the target recall; (2) refit the same settings
        on train+val; (3) score test and flag the riskiest <flag rate> share."""
        p_va = fitted_on_train.predict_proba(va[feats])[:, 1]
        p_te_trainonly = fitted_on_train.predict_proba(te[feats])[:, 1]
        rate = flag_rate_for_recall(va.is_late, p_va, args.target_recall)
        final = make_unfitted().fit(trva[feats], trva.is_late)
        p_te = final.predict_proba(te[feats])[:, 1]
        results[name] = {
            "model": label,
            "flag_rate_chosen_on_val": round(rate, 4),
            "val": evaluate(va.is_late, p_va, threshold_for_flag_rate(p_va, rate)),
            "test": evaluate(te.is_late, p_te, threshold_for_flag_rate(p_te, rate)),
            "test_without_refit": evaluate(te.is_late, p_te_trainonly,
                                           threshold_for_flag_rate(p_te_trainonly, rate)),
        }
        curves[label.split(" (")[0]] = p_te
        return final

    # 1) logistic regression baseline
    lr = logistic_baseline(num_cols, cat_cols).fit(tr[feats], tr.is_late)
    run("logistic_regression", "Logistic regression", lr, lambda: logistic_baseline(num_cols, cat_cols))

    # 2) gradient boosting
    as_category([tr, va, te], cat_cols)
    trva = pd.concat([tr, va])          # re-concat so categoricals match
    gbm_tr, gbm_name, gbm_factory = fit_gbm(tr[feats], tr.is_late, va[feats], va.is_late)
    gbm = run("gradient_boosting", gbm_name, gbm_tr, gbm_factory)

    # ---- report ---------------------------------------------------------
    rows = [["base rate (no model)", round(float(te.is_late.mean()), 4), 0.5, "-", "-", "-", "-", "-"]]
    for name, r in results.items():
        t, v = r["test"], r["val"]
        rows.append([name, t["pr_auc"], t["roc_auc"], v["pr_auc"], r["test_without_refit"]["pr_auc"],
                     t["recall"], t["precision"], t["flagged_pct"]])
    table = pd.DataFrame(rows, columns=["model", "test PR-AUC", "test ROC-AUC", "val PR-AUC",
                                        "test PR-AUC (no refit)", "test recall", "test precision", "flagged"])
    print(f"TEST — final models refit on train+val; flag rate chosen on val for recall >= {args.target_recall}")
    print(table.to_string(index=False))

    rep = ROOT / "reports"
    fig_dir = rep / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "models").mkdir(exist_ok=True)

    meta = {
        "split": {"val_start": str(vs.date()), "test_start": str(ts.date()),
                  "n_train": len(tr), "n_val": len(va), "n_test": len(te)},
        "target_recall": args.target_recall,
        "features": {"numeric": num_cols, "categorical": cat_cols},
        "results": results,
    }
    (rep / "model_metrics.json").write_text(json.dumps(meta, indent=2))
    md = ["| " + " | ".join(table.columns) + " |", "|" + "---|" * len(table.columns)]
    md += ["| " + " | ".join(str(v) for v in row) + " |" for row in table.itertuples(index=False)]
    (rep / "model_metrics.md").write_text("\n".join(md) + "\n")
    with open(ROOT / "models" / "gbm.pkl", "wb") as fh:
        pickle.dump({"model": gbm, "features": feats, "categorical": cat_cols, "prior": prior,
                     "flag_rate": results["gradient_boosting"]["flag_rate_chosen_on_val"]}, fh)

    # PR curves on test
    fig, ax = plt.subplots(figsize=(6, 4.5))
    for name, p in curves.items():
        prec, rec, _ = precision_recall_curve(te.is_late, p)
        ax.plot(rec, prec, label=f"{name} (AP={average_precision_score(te.is_late, p):.3f})")
    ax.axhline(te.is_late.mean(), ls="--", c="grey", lw=1, label=f"base rate {te.is_late.mean():.3f}")
    ax.set(xlabel="Recall", ylabel="Precision", title="Late delivery — test set PR curve", xlim=(0, 1), ylim=(0, 1))
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(fig_dir / "pr_curve.png", dpi=150)

    # Permutation importance (PR-AUC drop) on a validation sample
    sample = va.sample(min(len(va), 5000), random_state=SEED)
    imp = permutation_importance(gbm_tr, sample[feats], sample.is_late, scoring="average_precision",
                                 n_repeats=3, random_state=SEED)
    fi = (pd.DataFrame({"feature": feats, "importance": imp.importances_mean, "std": imp.importances_std})
            .sort_values("importance", ascending=False))
    fi.to_csv(rep / "feature_importance.csv", index=False)
    top = fi.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.barh(top.feature, top.importance, xerr=top["std"], color="#4C72B0")
    ax.set(xlabel="Drop in PR-AUC when shuffled", title="Top features (validation, permutation)")
    fig.tight_layout()
    fig.savefig(fig_dir / "feature_importance.png", dpi=150)

    print(f"\nSaved: reports/model_metrics.json, reports/feature_importance.csv, "
          f"reports/figures/*.png, models/gbm.pkl\nTop features: {', '.join(fi.feature.head(5))}")


if __name__ == "__main__":
    main()
