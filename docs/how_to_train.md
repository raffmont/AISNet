# How to Train AISNet Models

This guide shows how to download the **full AIS dataset** programmatically and prepare it for training vessel traffic prediction models. AISNet CSV files are hosted as daily/hourly files at:

```
https://data.meteo.uniparthenope.it/instruments/aisnet0/csv/YYYY/MM/DD/aisnet_YYYYMMDDZhh00.csv
```

Where:

- `YYYY` is the year (4 digits).
- `MM` is the month (01-12).
- `DD` is the day of the month (01-31).
- `hh` is the hour (00-23).

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

Download the CSV files by iterating through the timestamps you need. The examples below fetch a full day or a time range and store the raw CSVs in `data/raw/aisnet`.

### Python (download a date range by hour)

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

BASE_URL = "https://data.meteo.uniparthenope.it/instruments/aisnet0/csv"
OUTPUT_DIR = Path("data/raw/aisnet")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

start = datetime(2024, 1, 1, tzinfo=timezone.utc)
end = datetime(2024, 1, 2, tzinfo=timezone.utc)

current = start
while current < end:
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

### Bash (download a single day)

```bash
mkdir -p data/raw/aisnet

BASE_URL="https://data.meteo.uniparthenope.it/instruments/aisnet0/csv"
DAY="2024-01-01"

for hour in $(seq -w 0 23); do
  yyyy=$(date -u -d "$DAY" +"%Y")
  mm=$(date -u -d "$DAY" +"%m")
  dd=$(date -u -d "$DAY" +"%d")
  url="$BASE_URL/$yyyy/$mm/$dd/aisnet_${yyyy}${mm}${dd}Z${hour}00.csv"
  out="data/raw/aisnet/aisnet_${yyyy}${mm}${dd}Z${hour}00.csv"

  curl -sfL "$url" -o "$out" || echo "Missing $url"
done
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

If your data is in NMEA or AIS payload format, decode it into this schema and append to a single CSV or Parquet dataset. AISNet already logs CSV position reports with `TIMESTAMP, MMSI, LON, LAT, HEADING, SPEED` that can serve as a starting point. The hosted AISNet CSVs at the URL above use the same columns.

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

## End-to-end, ready-to-run Python example

The script below performs **all steps**: download the full dataset from the AISNet CSV server, normalize to a canonical schema, clean/filter, resample into trajectories, and split into train/validation/test. It uses only `requests` and `pandas` (plus the standard library).

> Tip: the full dataset is large. Run this on a machine with sufficient disk space and memory, or adjust the downloader to a smaller time range.

```python
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List
import io

import pandas as pd
import requests

BASE_URL = "https://data.meteo.uniparthenope.it/instruments/aisnet0/csv"
RAW_DIR = Path("data/raw/aisnet")
PROCESSED_DIR = Path("data/processed")
SPLITS_DIR = Path("data/splits")
RESAMPLE_FREQ = "5min"
MIN_POINTS = 20

RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
SPLITS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Link:
    href: str


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Link] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(Link(href=href))


def list_directory(url: str) -> list[str]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    parser = LinkParser()
    parser.feed(response.text)
    return [link.href for link in parser.links if link.href and not link.href.startswith("?")]


def iter_csv_urls(base_url: str) -> Iterable[str]:
    for year in list_directory(f"{base_url}/"):
        if not year.rstrip("/").isdigit():
            continue
        for month in list_directory(f"{base_url}/{year}"):
            if not month.rstrip("/").isdigit():
                continue
            for day in list_directory(f"{base_url}/{year}{month}"):
                if not day.rstrip("/").isdigit():
                    continue
                day_url = f"{base_url}/{year}{month}{day}"
                for filename in list_directory(day_url):
                    if filename.endswith(".csv"):
                        yield f"{day_url}{filename}"


def download_all_csvs() -> list[Path]:
    downloaded: list[Path] = []
    for url in iter_csv_urls(BASE_URL):
        filename = url.split("/")[-1]
        output_path = RAW_DIR / filename
        if output_path.exists():
            downloaded.append(output_path)
            continue
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        downloaded.append(output_path)
        print(f"Downloaded {output_path}")
    return downloaded


def normalize_and_clean(paths: Iterable[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        csv_bytes = path.read_bytes()
        frame = pd.read_csv(io.BytesIO(csv_bytes))
        frame = frame.rename(
            columns={
                "TIMESTAMP": "timestamp",
                "MMSI": "mmsi",
                "LAT": "lat",
                "LON": "lon",
                "HEADING": "heading",
                "SPEED": "sog",
            }
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp", "mmsi", "lat", "lon"])
        frame = frame[(frame["lat"].between(-90, 90)) & (frame["lon"].between(-180, 180))]
        frame = frame.drop_duplicates(subset=["timestamp", "mmsi", "lat", "lon"])
        for column in ["sog", "cog", "heading", "nav_status"]:
            if column not in frame.columns:
                frame[column] = pd.NA
        frames.append(
            frame[
                [
                    "timestamp",
                    "mmsi",
                    "lat",
                    "lon",
                    "sog",
                    "cog",
                    "heading",
                    "nav_status",
                ]
            ]
        )
    return pd.concat(frames, ignore_index=True)


def resample_trajectories(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["mmsi", "timestamp"])
    trajectories: list[pd.DataFrame] = []
    for mmsi, group in frame.groupby("mmsi"):
        group = group.set_index("timestamp").resample(RESAMPLE_FREQ).mean(numeric_only=True)
        group["mmsi"] = mmsi
        group = group.dropna(subset=["lat", "lon"])
        if len(group) >= MIN_POINTS:
            trajectories.append(group.reset_index())
    return pd.concat(trajectories, ignore_index=True)


def temporal_split(frame: pd.DataFrame) -> None:
    frame = frame.sort_values("timestamp")
    n_total = len(frame)
    train_end = int(n_total * 0.70)
    valid_end = int(n_total * 0.85)
    frame.iloc[:train_end].to_parquet(SPLITS_DIR / "train.parquet", index=False)
    frame.iloc[train_end:valid_end].to_parquet(SPLITS_DIR / "valid.parquet", index=False)
    frame.iloc[valid_end:].to_parquet(SPLITS_DIR / "test.parquet", index=False)


def main() -> None:
    raw_files = download_all_csvs()
    canonical = normalize_and_clean(raw_files)
    canonical.to_parquet(PROCESSED_DIR / "ais_positions_canonical.parquet", index=False)
    resampled = resample_trajectories(canonical)
    resampled.to_parquet(PROCESSED_DIR / "ais_positions_resampled.parquet", index=False)
    temporal_split(resampled)


if __name__ == "__main__":
    main()
```

## Next steps

- Train a sequence model (e.g., LSTM, Transformer) using the trajectories as input.
- Predict future positions (lat/lon) and optionally speed/course for a chosen horizon (e.g., 15–60 minutes).
- Evaluate with trajectory-aware metrics (e.g., ADE/FDE or Haversine distance).
