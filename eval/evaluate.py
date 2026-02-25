#!/usr/bin/env python3
"""
Minimal hackathon evaluator.

Reads a predictions CSV and writes a metrics CSV.

Default output schema:
  metric,value

Intended to be called by `master_eval.py` as:
  python3 eval/evaluate.py --pred <predictions.csv> --out <metrics.csv>
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(s: str) -> Optional[float]:
    try:
        x = float(s)
    except Exception:
        return None
    if math.isfinite(x):
        return x
    return None


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def _pick_prediction_column(fieldnames: Iterable[str]) -> Optional[str]:
    # Standardize on "prediction", but be forgiving for early tests.
    candidates = ["prediction", "pred", "score", "y_pred"]
    fields = list(fieldnames)
    for c in candidates:
        if c in fields:
            return c
    return None


def evaluate_predictions(pred_path: Path, expected_rows: Optional[int]) -> List[Tuple[str, str]]:
    if not pred_path.exists():
        raise FileNotFoundError(f"Predictions file not found: {pred_path}")
    if pred_path.stat().st_size == 0:
        raise ValueError(f"Predictions file is empty: {pred_path}")

    with pred_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Predictions CSV has no header row.")

        pred_col = _pick_prediction_column(reader.fieldnames)
        n_rows = 0
        n_missing_pred = 0
        numeric_vals: List[float] = []

        for row in reader:
            n_rows += 1
            if pred_col is None:
                continue
            raw = (row.get(pred_col) or "").strip()
            if raw == "":
                n_missing_pred += 1
                continue
            x = _to_float(raw)
            if x is not None:
                numeric_vals.append(x)

    metrics: List[Tuple[str, str]] = []
    metrics.append(("generated_at", _ts()))
    metrics.append(("predictions_path", str(pred_path)))
    metrics.append(("n_rows", str(n_rows)))
    metrics.append(("n_cols", str(len(reader.fieldnames))))
    metrics.append(("prediction_column", pred_col or ""))
    metrics.append(("n_missing_prediction", str(n_missing_pred if pred_col else n_rows)))
    metrics.append(("n_numeric_predictions", str(len(numeric_vals))))

    if expected_rows is not None:
        metrics.append(("expected_rows", str(expected_rows)))
        metrics.append(("rows_match_expected", str(n_rows == expected_rows)))

    if numeric_vals:
        metrics.append(("prediction_mean", f"{_mean(numeric_vals):.6f}"))
        metrics.append(("prediction_std", f"{_std(numeric_vals):.6f}"))
        metrics.append(("prediction_min", f"{min(numeric_vals):.6f}"))
        metrics.append(("prediction_max", f"{max(numeric_vals):.6f}"))

    # Basic “pass/fail” convenience flag for automation.
    ok = True
    if expected_rows is not None and n_rows != expected_rows:
        ok = False
    if pred_col is None:
        ok = False
    metrics.append(("ok", "1" if ok else "0"))
    return metrics


def write_metrics(out_path: Path, metrics: List[Tuple[str, str]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for k, v in metrics:
            w.writerow([k, v])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate predictions CSV and write metrics CSV.")
    p.add_argument("--pred", required=True, type=Path, help="Path to predictions CSV.")
    p.add_argument("--out", required=True, type=Path, help="Path to output metrics CSV.")
    p.add_argument("--expected-rows", type=int, default=None, help="Optional expected number of rows.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    metrics = evaluate_predictions(args.pred, args.expected_rows)
    write_metrics(args.out, metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

