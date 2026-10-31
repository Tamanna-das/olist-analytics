"""Rolling-origin backtest: how stable is late-delivery prediction over time?

For each fold, train on everything before the validation window, choose
GBM settings and the flag rate on validation, refit on train+val, then
score the next two-month test window. Same protocol as train.py.

    python src/backtest.py   ->  reports/backtest.csv, reports/figures/backtest.png
"""
from __future__ import annotations

import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from train import (add_history_rates, as_category, evaluate, fit_gbm, flag_rate_for_recall,
                   logistic_baseline, threshold_for_flag_rate)

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
TARGET_RECALL = 0.5

# (val_start, test_start, test_end): two-month validation, two-month test
FOLDS = [
    ("2017-10-01", "2017-12-01", "2018-02-01"),
    ("2017-12-01", "2018-02-01", "2018-04-01"),
    ("2018-02-01", "2018-04-01", "2018-06-01"),
    ("2018-04-01", "2018-06-01", "2018-09-01"),
]


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "features.csv", parse_dates=["purchase_ts"])
    rows = []
    for vs, ts, te_end in FOLDS:
        tr = df[df.purchase_ts < vs].copy()
        va = df[(df.purchase_ts >= vs) & (df.purchase_ts < ts)].copy()
        te = df[(df.purchase_ts >= ts) & (df.purchase_ts < te_end)].copy()
        rate_cols = add_history_rates([tr, va, te], float(tr.is_late.mean()))
        num, cat = NUMERIC_FEATURES + rate_cols, CATEGORICAL_FEATURES
        X = num + cat
        window = f"{ts[:7]} → {pd.Timestamp(te_end) - pd.Timedelta(days=1):%Y-%m}"

        lr = logistic_baseline(num, cat).fit(tr[X], tr.is_late)
        lr_rate = flag_rate_for_recall(va.is_late, lr.predict_proba(va[X])[:, 1], TARGET_RECALL)
        trva = pd.concat([tr, va])
        p_lr = logistic_baseline(num, cat).fit(trva[X], trva.is_late).predict_proba(te[X])[:, 1]

        as_category([tr, va, te], cat)
        trva = pd.concat([tr, va])
        g_tr, _, factory = fit_gbm(tr[X], tr.is_late, va[X], va.is_late)
        g_rate = flag_rate_for_recall(va.is_late, g_tr.predict_proba(va[X])[:, 1], TARGET_RECALL)
        p_g = factory().fit(trva[X], trva.is_late).predict_proba(te[X])[:, 1]

        e_lr = evaluate(te.is_late, p_lr, threshold_for_flag_rate(p_lr, lr_rate))
        e_g = evaluate(te.is_late, p_g, threshold_for_flag_rate(p_g, g_rate))
        rows.append({
            "test_window": window, "n_test": len(te), "late_rate": round(te.is_late.mean(), 4),
            "lr_pr_auc": e_lr["pr_auc"], "gbm_pr_auc": e_g["pr_auc"],
            "lr_roc_auc": e_lr["roc_auc"], "gbm_roc_auc": e_g["roc_auc"],
            "gbm_lift": round(e_g["pr_auc"] / te.is_late.mean(), 2),
            "gbm_recall": e_g["recall"], "gbm_precision": e_g["precision"], "gbm_flagged": e_g["flagged_pct"],
            "avg_promised_days": round(te.promised_days.mean(), 1),
        })
        print(rows[-1])

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "reports" / "backtest.csv", index=False)
    print("\n" + out.to_string(index=False))

    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(out))
    ax.plot(x, out.gbm_pr_auc, marker="o", label="Gradient boosting PR-AUC")
    ax.plot(x, out.lr_pr_auc, marker="s", label="Logistic PR-AUC")
    ax.plot(x, out.late_rate, ls="--", c="grey", label="Base rate (late share)")
    ax.set_xticks(list(x), out.test_window, rotation=15)
    ax.set(ylabel="PR-AUC", title="Rolling backtest: two-month test windows")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(ROOT / "reports" / "figures" / "backtest.png", dpi=150)


if __name__ == "__main__":
    main()
