# AISNet

AISNet is a lightweight TCP server that receives NMEA 0183 sentences (including AIS `!AIVDM/!AIVDO`) over TCP, logs them, saves raw sentences to rotating `.nmea` files, decodes AIS messages, appends AIS position reports to rotating `.csv` files, and can optionally **repeat** raw AIS sentences to another server via TCP or UDP.

---

## Features

- TCP server listening on a configurable port (default `2000`)
- Receives NMEA sentences line-by-line (`\n` or `\r\n`)
- Console logging via Python `logging`
- Rotating raw NMEA output:
  - `aisnet_YYYYMMDDZhhmmss.nmea` (UTC)
- Rotating CSV output for AIS position reports only (types `1,2,3,18,19,27`):
  - `aisnet_YYYYMMDDZhhmmss.csv` (UTC)
  - Columns: `TIMESTAMP, MMSI, LON, LAT, HEADING, SPEED`
  - `TIMESTAMP` is ISO 8601 UTC (e.g. `2026-01-16T11:03:52Z`)
- File rotation happens:
  - at each server start (new files)
  - then every configured `rotate_seconds`
- **Repeater (optional):**
  - If `repeater.remoteHost` and `repeater.remotePort` are set, each received raw AIS sentence
    (`!AIVDM/!AIVDO`) is forwarded to the remote endpoint using **TCP** (`tcpip`) or **UDP** (`udp`).
  - For UDP, you can enable `broadcast: true` (sets `SO_BROADCAST`).

---

## Configuration (`config.json`)

### Minimal example (no repeater)

```json
{
  "server": { "port": 2000 },
  "output": {
    "nmea": { "path": "data/nmea_logs", "rotate_seconds": 300 },
    "csv":  { "path": "data/csv_logs",  "rotate_seconds": 300 }
  }
}
```

### Example with repeater (TCP)

```json
{
  "server": { "port": 2000 },
  "repeater": {
    "remoteHost": "193.205.230.7",
    "remotePort": 2000,
    "protocol": "tcpip"
  },
  "output": {
    "nmea": { "path": "data/nmea_logs", "rotate_seconds": 300 },
    "csv":  { "path": "data/csv_logs",  "rotate_seconds": 300 }
  }
}
```

### Example with repeater (UDP + broadcast)

```json
{
  "server": { "port": 2000 },
  "repeater": {
    "remoteHost": "255.255.255.255",
    "remotePort": 2000,
    "protocol": "udp",
    "broadcast": true
  },
  "output": {
    "nmea": { "path": "data/nmea_logs", "rotate_seconds": 300 },
    "csv":  { "path": "data/csv_logs",  "rotate_seconds": 300 }
  }
}
```

### Note about relative output paths and Docker

If you use relative paths like:

```json
"path": "data/nmea_logs"
```

they are resolved relative to the **working directory**. In Docker, the working directory is `/app`, so outputs go to:

- `/app/data/nmea_logs`
- `/app/data/csv_logs`

To persist outputs, mount a host folder (or volume) to `/app/data`.

---

## Local run (no Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p data/nmea_logs data/csv_logs
python3 main.py -c config.json
```

Test:
```bash
nc 127.0.0.1 2000
```

---

## Docker

### Build
```bash
docker build -t aisnet:latest .
```

### Run (bind mount `./data` to persist outputs)
Because `config.json` writes to `data/...`, mount a host directory to `/app/data`:

```bash
mkdir -p data/nmea_logs data/csv_logs

docker run --rm -it \
  -p 2000:2000/tcp \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/data:/app/data" \
  aisnet:latest
```

---

## Docker Compose

Create `docker-compose.yml`:

```yaml
services:
  aisnet:
    build: .
    container_name: aisnet
    restart: unless-stopped

    ports:
      - "2000:2000/tcp"

    volumes:
      - ./config.json:/app/config.json:ro
      - ./data:/app/data

    command: ["python3", "/app/main.py", "-c", "/app/config.json"]
```

Start:
```bash
mkdir -p data/nmea_logs data/csv_logs
docker compose up -d --build
```

Logs:
```bash
docker compose logs -f
```

Stop:
```bash
docker compose down
```

---

## License

MIT (see `LICENSE`).
