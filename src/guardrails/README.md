# Guardrail metrics (minimal)

This folder contains the **minimal code** required by `scripts/evaluate.sh`.

`scripts/evaluate.sh` runs:

```bash
PYTHONPATH=. python -m src.guardrails.get_guardrail_metrics --predictions <predictions.csv> --output <out_dir>/eval_metrics.json
```

That command:
- reads a predictions CSV
- computes precision/recall/F1 (+ latency if present)
- writes:
  - the JSON metrics at the `--output` path
  - a one-row `metrics.csv` in the same directory as `--output`

## Predictions CSV contract

The predictions CSV must include:
- **`combined_pred`**: prediction (truthy/falsey; `1/0`, `true/false`, `yes/no`, `harmful/safe`)
- **`label_harmful`** or **`label`**: ground-truth label (same accepted formats)

Optional:
- **`latency_ms`**: numeric latency per sample in milliseconds (used for mean/total latency metrics)

## Files kept in this repo

- `metrics.py`: `GuardrailMetricsResult` + `compute_metrics_from_predictions()`
- `get_guardrail_metrics.py`: CLI entrypoint used by `scripts/evaluate.sh`
