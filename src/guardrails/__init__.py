"""Guardrails package (trimmed).

This repo's evaluation flow uses only `python -m src.guardrails.get_guardrail_metrics`
to compute metrics from a predictions CSV.
"""

from .metrics import GuardrailMetricsResult, compute_metrics_from_predictions

__all__ = ["GuardrailMetricsResult", "compute_metrics_from_predictions"]