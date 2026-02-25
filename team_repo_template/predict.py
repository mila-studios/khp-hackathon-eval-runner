from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Team prediction script (called by scripts/predict.sh).")
    p.add_argument("--input", required=True, type=Path, help="Path to input CSV provided by the runner.")
    p.add_argument("--output", required=True, type=Path, help="Where to write predictions CSV.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    in_path = args.input
    out_path = args.output

    if not in_path.exists() or in_path.stat().st_size == 0:
        raise SystemExit(f"Input CSV missing/empty: {in_path}")

    with in_path.open(newline="", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header row")

        fieldnames = list(reader.fieldnames)
        if "prediction" not in fieldnames:
            fieldnames.append("prediction")

        os.makedirs(out_path.parent, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                row = dict(row)

                # Replace this with your real model inference.
                row["prediction"] = row.get("prediction") or "0.5"

                writer.writerow(row)

    print(f"Wrote predictions: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

