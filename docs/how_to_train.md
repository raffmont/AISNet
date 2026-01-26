# How to Train AISNet Models

This guide shows how to download the **full AIS dataset** programmatically and prepare it for training vessel traffic prediction models. The examples assume the dataset is hosted as a single archive (or a set of archives) behind a URL or API token. Replace the placeholders with your data provider details.

## Assumptions and folder layout

Create a consistent layout so scripts stay portable:

```
AISNet/
├── data/
│   ├── raw/
│   ├── processed/
│   └── splits/
└── docs/
```

- `data/raw` contains the downloaded archives and original files.
- `data/processed` contains normalized, deduplicated, and cleaned AIS records.
- `data/splits` contains train/validation/test splits.

## Download the full dataset

> Replace `DATASET_URL`, `DATASET_TOKEN`, and filenames with your provider’s details.

### Python (streaming download with checksum verification)

```python
import hashlib
import os
from pathlib import Path
import requests

DATASET_URL = "https://example-provider.com/ais/full-dataset.tar.zst"
DATASET_TOKEN = os.environ.get("DATASET_TOKEN")
OUTPUT_DIR = Path("data/raw")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "full-dataset.tar.zst"
EXPECTED_SHA256 = "<replace-with-checksum>"

headers = {"Authorization": f"Bearer {DATASET_TOKEN}"} if DATASET_TOKEN else {}

with requests.get(DATASET_URL, headers=headers, stream=True, timeout=60) as response:
    response.raise_for_status()
    with open(OUTPUT_FILE, "wb") as file_handle:
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if chunk:
                file_handle.write(chunk)

sha256 = hashlib.sha256()
with open(OUTPUT_FILE, "rb") as file_handle:
    for chunk in iter(lambda: file_handle.read(8 * 1024 * 1024), b""):
        sha256.update(chunk)

if EXPECTED_SHA256 != "<replace-with-checksum>" and sha256.hexdigest() != EXPECTED_SHA256:
    raise ValueError("Checksum mismatch; download may be corrupted.")

print(f"Downloaded {OUTPUT_FILE}")
```

### Bash (curl + checksum)

```bash
mkdir -p data/raw
export DATASET_URL="https://example-provider.com/ais/full-dataset.tar.zst"
export OUTPUT_FILE="data/raw/full-dataset.tar.zst"
export EXPECTED_SHA256="<replace-with-checksum>"

curl -L "$DATASET_URL" -o "$OUTPUT_FILE"

if [ "$EXPECTED_SHA256" != "<replace-with-checksum>" ]; then
  echo "$EXPECTED_SHA256  $OUTPUT_FILE" | sha256sum --check -
fi
```

### Ruby (streaming download + checksum)

```ruby
require "digest"
require "fileutils"
require "net/http"
require "uri"

dataset_url = ENV.fetch("DATASET_URL", "https://example-provider.com/ais/full-dataset.tar.zst")
output_dir = File.join("data", "raw")
FileUtils.mkdir_p(output_dir)
output_file = File.join(output_dir, "full-dataset.tar.zst")
expected_sha256 = ENV.fetch("EXPECTED_SHA256", "<replace-with-checksum>")

uri = URI.parse(dataset_url)
Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https") do |http|
  request = Net::HTTP::Get.new(uri)
  http.request(request) do |response|
    raise "Download failed: #{response.code}" unless response.is_a?(Net::HTTPSuccess)

    File.open(output_file, "wb") do |file|
      response.read_body do |chunk|
        file.write(chunk)
      end
    end
  end
end

digest = Digest::SHA256.file(output_file).hexdigest
if expected_sha256 != "<replace-with-checksum>" && digest != expected_sha256
  raise "Checksum mismatch; download may be corrupted."
end

puts "Downloaded #{output_file}"
```

## Prepare the dataset for training

AIS data often arrives as NMEA sentences, raw AIS message payloads, or position-report CSVs. The goal is to convert everything into a **time-ordered sequence of vessel positions** with consistent units and coordinates.

### 1) Extract and stage raw files

If you received archives, extract them to `data/raw`:

```bash
mkdir -p data/raw/extracted
# Example for .tar.zst archives (requires zstd)
# tar --use-compress-program=unzstd -xf data/raw/full-dataset.tar.zst -C data/raw/extracted
```

### 2) Normalize to a canonical schema

Recommended canonical columns:

| Column | Type | Notes |
| --- | --- | --- |
| timestamp | ISO 8601 UTC | Use a single timezone (UTC) |
| mmsi | integer | Vessel identifier |
| lat | float | WGS84 latitude |
| lon | float | WGS84 longitude |
| sog | float | Speed over ground (knots) |
| cog | float | Course over ground (degrees) |
| heading | float | Heading (degrees) |
| nav_status | integer | AIS navigational status |

If your data is in NMEA or AIS payload format, decode it into this schema and append to a single CSV or Parquet dataset. AISNet already logs CSV position reports with `TIMESTAMP, MMSI, LON, LAT, HEADING, SPEED` that can serve as a starting point.

### 3) Clean and filter

- Drop invalid coordinates (lat not in `[-90, 90]`, lon not in `[-180, 180]`).
- Remove duplicate messages (same `timestamp`, `mmsi`, `lat`, `lon`).
- Filter for position-report message types (1, 2, 3, 18, 19, 27) if your data contains multiple message types.
- Normalize units (e.g., ensure `sog` is in knots, `cog/heading` in degrees).

### 4) Resample into trajectories

For prediction tasks, build fixed-length trajectories per vessel:

- Sort by `mmsi` then `timestamp`.
- Resample to a fixed interval (e.g., every 1–5 minutes).
- Interpolate missing points if gaps are short; otherwise split into separate voyages.
- Remove trajectories shorter than a minimum length (e.g., 20 points).

### 5) Split into train/validation/test

A robust split is **temporal** to avoid leakage:

- Train: earliest 70%
- Validation: next 15%
- Test: most recent 15%

Keep entire vessel trajectories within a split when possible to avoid mixing future information.

## Practical preparation example (Python)

This example uses `pandas` to clean, resample, and split. Adapt the input path to your decoded CSVs.

```python
from pathlib import Path
import pandas as pd

raw_path = Path("data/raw/extracted/ais_positions.csv")
processed_dir = Path("data/processed")
processed_dir.mkdir(parents=True, exist_ok=True)

# Load raw data
frame = pd.read_csv(raw_path)

# Rename to canonical columns if needed
frame = frame.rename(columns={
    "TIMESTAMP": "timestamp",
    "MMSI": "mmsi",
    "LAT": "lat",
    "LON": "lon",
    "HEADING": "heading",
    "SPEED": "sog",
})

# Parse timestamps and clean
frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
frame = frame.dropna(subset=["timestamp", "mmsi", "lat", "lon"])
frame = frame[(frame["lat"].between(-90, 90)) & (frame["lon"].between(-180, 180))]
frame = frame.drop_duplicates(subset=["timestamp", "mmsi", "lat", "lon"])

# Sort and resample per vessel (example: 5-minute grid)
frame = frame.sort_values(["mmsi", "timestamp"])

trajectories = []
for mmsi, group in frame.groupby("mmsi"):
    group = group.set_index("timestamp").resample("5min").mean(numeric_only=True)
    group["mmsi"] = mmsi
    group = group.dropna(subset=["lat", "lon"])
    if len(group) >= 20:
        trajectories.append(group.reset_index())

processed = pd.concat(trajectories, ignore_index=True)
processed.to_parquet(processed_dir / "ais_positions_resampled.parquet", index=False)

# Temporal split
processed = processed.sort_values("timestamp")

n_total = len(processed)
train_end = int(n_total * 0.70)
valid_end = int(n_total * 0.85)

splits_dir = Path("data/splits")
splits_dir.mkdir(parents=True, exist_ok=True)

processed.iloc[:train_end].to_parquet(splits_dir / "train.parquet", index=False)
processed.iloc[train_end:valid_end].to_parquet(splits_dir / "valid.parquet", index=False)
processed.iloc[valid_end:].to_parquet(splits_dir / "test.parquet", index=False)
```

## Next steps

- Train a sequence model (e.g., LSTM, Transformer) using the trajectories as input.
- Predict future positions (lat/lon) and optionally speed/course for a chosen horizon (e.g., 15–60 minutes).
- Evaluate with trajectory-aware metrics (e.g., ADE/FDE or Haversine distance).
