#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"

usage() {
  echo "Usage: $0 <input.csv> <predictions.csv>" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
IN_CSV="$1"
OUT_CSV="$2"

PYTHON_BIN="python3"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  PYTHON_BIN="$VENV_DIR/bin/python"
fi

echo "== predict =="
echo "Input : $IN_CSV"
echo "Output: $OUT_CSV"
echo "Mode  : ${HACKATHON_MODE:-unknown} (gpu=${HACKATHON_NEEDS_GPU:-0} llm_judge=${HACKATHON_NEEDS_LLM_JUDGE:-0})"

# Most teams only need to edit predict.py.
if [[ -f predict.py ]]; then
  "$PYTHON_BIN" predict.py --input "$IN_CSV" --output "$OUT_CSV"
  echo "OK"
  exit 0
fi

# Fallback (shouldn't happen unless predict.py was deleted).
"$PYTHON_BIN" - <<'PY' "$IN_CSV" "$OUT_CSV"
import csv
import os
import sys

in_path, out_path = sys.argv[1], sys.argv[2]
if not os.path.exists(in_path) or os.path.getsize(in_path) == 0:
    raise SystemExit(f"Input CSV missing/empty: {in_path}")

with open(in_path, newline="", encoding="utf-8") as f_in:
    reader = csv.DictReader(f_in)
    if not reader.fieldnames:
        raise SystemExit("Input CSV has no header row")

    fieldnames = list(reader.fieldnames)
    if "prediction" not in fieldnames:
        fieldnames.append("prediction")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            row = dict(row)
            row["prediction"] = row.get("prediction") or "0.5"
            writer.writerow(row)

print(f"Wrote predictions: {out_path}")
PY

echo "OK"

