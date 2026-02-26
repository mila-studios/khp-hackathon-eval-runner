## Team submission template

Copy this folder into a new git repo for a team submission.

### Required files

- `hackathon.json` (required): declares resource needs
- `scripts/configure.sh` (required)
- `scripts/predict.sh` (required)

### “Edit these files” (recommended)

- `requirements.txt` (optional): Python dependencies for your model
- `predict.py` (recommended): where you implement the actual prediction logic

### Contract

- `scripts/configure.sh`: takes no args
- `scripts/predict.sh <input.csv> <predictions.csv>`: must write the output CSV exactly at the path provided

The runner will pass:
- `HACKATHON_NEEDS_GPU=0|1`
- `HACKATHON_MODE=cpu|gpu`

