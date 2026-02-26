#!/usr/bin/env python3
"""Compute precision, recall, F1 and latency from a prediction CSV.

Usage:
    cd project && PYTHONPATH=. python -m src.guardrails.get_guardrail_metrics \\
        --predictions-dir results/ \\
        [--output results/metrics.json]

    Or specify file explicitly:
    python -m src.guardrails.get_guardrail_metrics \\
        --predictions results/predictions_input.csv \\
        --output results/metrics.json

When --output is set, writes:
  - <output>.json (full metrics JSON)
  - <output_dir>/metrics.csv (precision, recall, F1, latency)

Prediction CSV format: must have columns label_harmful (or label) and combined_pred;
optional latency_ms.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root = project/ (parent of src/)
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .metrics import GuardrailMetricsResult, compute_metrics_from_predictions


def _load_predictions_csv(csv_path: Path) -> List[Dict[str, Any]]:
    """Load prediction CSV; preserve numeric/string values (metrics layer normalizes)."""
    rows: List[Dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        for row in reader:
            # Keep values as-is; compute_metrics_from_predictions handles 0/1 and labels
            rows.append(dict(row))
    return rows


def _metrics_to_dict(m: GuardrailMetricsResult) -> Dict[str, Any]:
    """Serialize GuardrailMetricsResult for JSON output."""
    d: Dict[str, Any] = {
        "precision": m.precision,
        "recall": m.recall,
        "f1": m.f1,
        "support_harmful": m.support_harmful,
        "support_safe": m.support_safe,
        "total_samples": m.total_samples,
        "guardrail_names": m.guardrail_names,
    }
    if m.latency_ms_mean is not None:
        d["latency_ms_mean"] = m.latency_ms_mean
    if m.latency_ms_total is not None:
        d["latency_ms_total"] = m.latency_ms_total
    if m.latency_ms_per_sample is not None:
        d["latency_ms_per_sample_count"] = len(m.latency_ms_per_sample)
    return d


def _write_metrics_csv(
    rows: List[Dict[str, Any]],
    csv_path: Path,
) -> None:
    """Write one row with precision, recall, F1, latency."""
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_metrics(
    predictions_path: Optional[Path] = None,
    predictions_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load predictions CSV, compute metrics, optionally write JSON and metrics CSV.

    Either pass predictions_dir (uses predictions.csv) or predictions_path.
    """
    if predictions_dir is not None:
        predictions_dir = predictions_dir.resolve()
        if predictions_path is None:
            p = predictions_dir / "predictions.csv"
            if p.exists():
                predictions_path = p

    if predictions_path is None:
        raise ValueError("Provide --predictions-dir or --predictions")

    results: Dict[str, Any] = {
        "total_samples": None,
        "guardrail": None,
    }
    metrics_csv_rows: List[Dict[str, Any]] = []
    out_dir = output_path.parent if output_path else None

    if predictions_path.exists():
        predictions = _load_predictions_csv(predictions_path)
        if predictions:
            metrics = compute_metrics_from_predictions(
                predictions,
                combined_pred_key="combined_pred",
                label_key="label_harmful",
                fallback_label_key="label",
                latency_key="latency_ms",
            )
            results["guardrail"] = _metrics_to_dict(metrics)
            results["total_samples"] = metrics.total_samples
            metrics_csv_rows.append({
                "precision": metrics.precision,
                "recall": metrics.recall,
                "f1": metrics.f1,
                "latency_ms_mean": metrics.latency_ms_mean if metrics.latency_ms_mean is not None else "",
                "latency_ms_total": metrics.latency_ms_total if metrics.latency_ms_total is not None else "",
                "support_harmful": metrics.support_harmful,
                "support_safe": metrics.support_safe,
                "total_samples": metrics.total_samples,
                "guardrail_names": "|".join(metrics.guardrail_names),
            })

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        if out_dir is not None and metrics_csv_rows:
            _write_metrics_csv(metrics_csv_rows, out_dir / "metrics.csv")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute precision, recall, F1 and latency from prediction CSVs."
    )
    parser.add_argument(
        "--predictions-dir",
        "-p",
        default=None,
        help="Directory containing predictions.csv.",
    )
    parser.add_argument(
        "--predictions",
        default=None,
        help="Path to predictions CSV (overrides predictions-dir).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional path to write JSON metrics (and metrics.csv in same dir).",
    )
    args = parser.parse_args()

    predictions_dir = Path(args.predictions_dir).expanduser().resolve() if args.predictions_dir else None
    predictions_path = Path(args.predictions).expanduser().resolve() if args.predictions else None
    output_path = Path(args.output).expanduser().resolve() if args.output else None

    if predictions_dir is not None and not predictions_dir.exists():
        print(f"Predictions dir not found: {predictions_dir}", file=sys.stderr)
        return 1

    try:
        results = run_metrics(
            predictions_path=predictions_path,
            predictions_dir=predictions_dir,
            output_path=output_path,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Print summary
    print("Guardrail metrics (positive class = harmful)")
    print("=" * 50)
    if results.get("guardrail"):
        m = results["guardrail"]
        if "error" in m:
            print("Guardrail:", m["error"])
        else:
            print(f"  Precision: {m['precision']:.4f}  Recall: {m['recall']:.4f}  F1: {m['f1']:.4f}")
            if "latency_ms_mean" in m:
                print(f"  Latency: mean={m['latency_ms_mean']:.2f} ms  total={m['latency_ms_total']:.2f} ms")
            print(f"  Samples: {m['total_samples']} (harmful={m['support_harmful']}, safe={m['support_safe']})")
            if m.get("guardrail_names"):
                print(f"  Guardrails: {m['guardrail_names']}")

    if output_path:
        out_dir = output_path.parent
        print(f"Metrics JSON: {output_path}")
        if (out_dir / "metrics.csv").exists():
            print(f"Metrics CSV: {out_dir / 'metrics.csv'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
