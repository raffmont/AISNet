# How to Prepare AISNet Data for Prediction

This guide explains how to download **recent AISNet CSV files** and prepare them for making predictions (inference). It mirrors the style of the training guide, but focuses on fetching only the **last N seconds** of data from the hosted hourly CSVs.

AISNet CSVs are available at:

```
https://data.meteo.uniparthenope.it/instruments/aisnet0/csv/YYYY/MM/DD/aisnet_YYYYMMDDZhh00.csv
```

Where:

- `YYYY` is the year (4 digits).
- `MM` is the month (01-12).
- `DD` is the day of the month (01-31).
- `hh` is the hour (00-23).

## Assumptions and folder layout

```
AISNet/
├── data/
│   ├── raw/
│   ├── processed/
│   └── predictions/
└── docs/
```

- `data/raw` holds the downloaded hourly CSVs.
- `data/processed` holds cleaned, normalized files for inference.
- `data/predictions` is where model outputs can be written.

## Download the last N seconds of data

Because the files are hourly, the simplest approach is:

1. Compute the UTC time window (`now - seconds` to `now`).
2. Download every hourly CSV that intersects the window.
3. Filter records to the exact time window during preprocessing.

### Python (download recent hours for a window)

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

BASE_URL = "https://data.meteo.uniparthenope.it/instruments/aisnet0/csv"
OUTPUT_DIR = Path("data/raw/aisnet")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

seconds_back = 6 * 3600  # last 6 hours
now = datetime.now(timezone.utc)
start = now - timedelta(seconds=seconds_back)

# Iterate over hours that intersect the window
current = start.replace(minute=0, second=0, microsecond=0)
while current <= now:
    url = (
        f"{BASE_URL}/{current:%Y}/{current:%m}/{current:%d}/"
        f"aisnet_{current:%Y%m%d}Z{current:%H}00.csv"
    )
    out_file = OUTPUT_DIR / f"aisnet_{current:%Y%m%d}Z{current:%H}00.csv"

    response = requests.get(url, timeout=60)
    if response.status_code == 200:
        out_file.write_bytes(response.content)
        print(f"Downloaded {out_file}")
    else:
        print(f"Missing {url} -> {response.status_code}")

    current += timedelta(hours=1)
```

### Bash (download recent hours for a window)

```bash
mkdir -p data/raw/aisnet

BASE_URL="https://data.meteo.uniparthenope.it/instruments/aisnet0/csv"
SECONDS_BACK=$((6 * 3600))

start_epoch=$(date -u +%s)
start_epoch=$((start_epoch - SECONDS_BACK))

start_hour=$(date -u -d "@$start_epoch" +"%Y-%m-%d %H:00:00")
end_hour=$(date -u +"%Y-%m-%d %H:00:00")

current="$start_hour"
while [ "$(date -u -d "$current" +%s)" -le "$(date -u -d "$end_hour" +%s)" ]; do
  yyyy=$(date -u -d "$current" +"%Y")
  mm=$(date -u -d "$current" +"%m")
  dd=$(date -u -d "$current" +"%d")
  hh=$(date -u -d "$current" +"%H")

  url="$BASE_URL/$yyyy/$mm/$dd/aisnet_${yyyy}${mm}${dd}Z${hh}00.csv"
  out="data/raw/aisnet/aisnet_${yyyy}${mm}${dd}Z${hh}00.csv"

  curl -sfL "$url" -o "$out" || echo "Missing $url"
  current=$(date -u -d "$current +1 hour" +"%Y-%m-%d %H:00:00")
done
```

## Prepare the data for prediction

Prediction workflows typically need the **latest, cleaned trajectories** per vessel. The steps below keep only the requested window and prepare a single file for inference.

### 1) Normalize to the canonical schema

AISNet CSVs already have the columns:

- `TIMESTAMP` (ISO 8601 UTC)
- `MMSI`
- `LON`
- `LAT`
- `HEADING`
- `SPEED`

Rename these into the canonical schema used in training (`timestamp`, `mmsi`, `lon`, `lat`, `heading`, `sog`).

### 2) Filter to the exact time window

Because you downloaded hourly files, filter to the exact `(now - seconds)` window so you do not feed extra history into prediction.

### 3) Clean and order

- Drop invalid coordinates.
- Drop duplicates (`timestamp`, `mmsi`, `lat`, `lon`).
- Sort by `mmsi` and `timestamp`.

### 4) Save a recent slice for inference

Write the cleaned slice to a single Parquet or CSV file to feed into your model.

## Practical preparation example (Python)

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

raw_dir = Path("data/raw/aisnet")
processed_dir = Path("data/processed")
processed_dir.mkdir(parents=True, exist_ok=True)

seconds_back = 6 * 3600
now = datetime.now(timezone.utc)
start = now - timedelta(seconds=seconds_back)

frames = []
for path in sorted(raw_dir.glob("aisnet_*.csv")):
    frame = pd.read_csv(path)
    frames.append(frame)

if not frames:
    raise SystemExit("No raw files found; download recent data first.")

frame = pd.concat(frames, ignore_index=True)
frame = frame.rename(columns={
    "TIMESTAMP": "timestamp",
    "MMSI": "mmsi",
    "LAT": "lat",
    "LON": "lon",
    "HEADING": "heading",
    "SPEED": "sog",
})

frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
frame = frame.dropna(subset=["timestamp", "mmsi", "lat", "lon"])
frame = frame[(frame["lat"].between(-90, 90)) & (frame["lon"].between(-180, 180))]
frame = frame.drop_duplicates(subset=["timestamp", "mmsi", "lat", "lon"])

# Filter to the exact recent window
frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= now)]

# Sort for inference
frame = frame.sort_values(["mmsi", "timestamp"])

output_file = processed_dir / "ais_recent.parquet"
frame.to_parquet(output_file, index=False)
print(f"Prepared {output_file} with {len(frame)} rows")
```

## Next steps

- Feed the recent trajectories into your prediction pipeline.
- Store outputs in `data/predictions` for downstream visualization or alerts.
