"""Compute precision, recall, F1 and latency from a predictions list/CSV.

This file is intentionally **metrics-only** in this repo: no guardrail
implementations, no pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _label_to_bool(label: Any) -> bool:
    """Convert various label formats to bool (True = harmful)."""
    if isinstance(label, bool):
        return label
    if isinstance(label, int):
        return label != 0
    if isinstance(label, str):
        v = label.strip().lower()
        if v in ("yes", "true", "1", "harmful", "unsafe"):
            return True
        if v in ("no", "false", "0", "safe"):
            return False
    return bool(label)


@dataclass
class GuardrailMetricsResult:
    """Result of guardrail metrics computation."""

    precision: float
    recall: float
    f1: float
    support_harmful: int  # number of true harmful samples
    support_safe: int     # number of true safe samples
    total_samples: int
    # Latency (ms): for the whole stack per sample
    latency_ms_mean: Optional[float] = None
    latency_ms_total: Optional[float] = None
    latency_ms_per_sample: Optional[List[float]] = None
    guardrail_names: List[str] = field(default_factory=list)


def _pred_to_bool(v: Any) -> bool:
    """Convert a prediction value from CSV or dict to bool (True = harmful)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "harmful")
    return bool(v)


def compute_metrics_from_predictions(
    predictions: List[Dict[str, Any]],
    *,
    combined_pred_key: str = "combined_pred",
    label_key: str = "label_harmful",
    fallback_label_key: str = "label",
    latency_key: str = "latency_ms",
    guardrail_names: Optional[List[str]] = None,
) -> GuardrailMetricsResult:
    """
    Compute precision, recall, F1 and optional latency from a list of prediction rows.

    Use this when you already have per-sample predictions. Each row must have
    the ground-truth label (harmful or not) and the combined prediction.

    Args:
        predictions: List of dicts. Each must have combined_pred (or combined_pred_key)
            and label (label_key or fallback_label_key). Optional: latency_key for
            per-sample latency in ms.
        combined_pred_key: Key for the guardrail(s) combined prediction (bool or 0/1).
        label_key: Key for ground-truth harmful flag (bool or 0/1). If missing,
            fallback_label_key is used and converted via _label_to_bool.
        fallback_label_key: Key for raw label when label_key is absent.
        latency_key: Key for per-sample latency in ms (optional).
        guardrail_names: Optional list of guardrail names for the result.

    Returns:
        GuardrailMetricsResult with precision, recall, F1, support, and latency
        stats if latency_key is present in the rows.
    """
    if not predictions:
        return GuardrailMetricsResult(
            precision=0.0,
            recall=0.0,
            f1=0.0,
            support_harmful=0,
            support_safe=0,
            total_samples=0,
            guardrail_names=guardrail_names or [],
        )

    y_true: List[bool] = []
    y_pred: List[bool] = []
    latencies_ms: List[float] = []

    for row in predictions:
        label_val = row.get(label_key)
        if label_val is None:
            label_val = row.get(fallback_label_key)
            label_harmful = _label_to_bool(label_val)
        else:
            label_harmful = _pred_to_bool(label_val)
        pred_val = row.get(combined_pred_key)
        combined_pred = _pred_to_bool(pred_val) if pred_val is not None else False

        y_true.append(label_harmful)
        y_pred.append(combined_pred)

        if latency_key in row and row[latency_key] is not None:
            try:
                latencies_ms.append(float(row[latency_key]))
            except (TypeError, ValueError):
                pass

    tp = sum(1 for t, p in zip(y_true, y_pred) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred) if not t and p)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t and not p)
    support_harmful = sum(y_true)
    support_safe = len(y_true) - support_harmful

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    latency_ms_mean = (
        float(sum(latencies_ms)) / len(latencies_ms) if latencies_ms else None
    )
    latency_ms_total = sum(latencies_ms) if latencies_ms else None

    return GuardrailMetricsResult(
        precision=precision,
        recall=recall,
        f1=f1,
        support_harmful=support_harmful,
        support_safe=support_safe,
        total_samples=len(y_true),
        latency_ms_mean=latency_ms_mean,
        latency_ms_total=latency_ms_total,
        latency_ms_per_sample=latencies_ms if latencies_ms else None,
        guardrail_names=guardrail_names or [],
    )
