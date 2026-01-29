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
  - then at 00 minutes of each UTC hour
- Optional web server that hosts output files for download (`/nmea` and `/csv`)
- **Repeater (optional):**
  - If `repeater.remoteHost` and `repeater.remotePort` are set, each received raw AIS sentence
    (`!AIVDM/!AIVDO`) is forwarded to the remote endpoint using **TCP** (`tcpip`) or **UDP** (`udp`).
  - For UDP, you can enable `broadcast: true` (sets `SO_BROADCAST`).
  - Set `repeater.mode` to `server` to listen for TCP clients and forward AIS sentences to each connected client.
  - Set `repeater.enabled` to `false` to keep the configuration present but disable the repeater.

---

## Documentation

- [Getting Started](docs/getting_started.md)
- [Installation](docs/install.md)
- [How to Train AISNet Models](docs/how_to_train.md)
- [How to Prepare AISNet Data for Prediction](docs/how_to_predict.md)

---

## Configuration (`config.json`)

### Minimal example (no repeater)

```json
{
  "server": { "port": 2000 },
  "output": {
    "nmea": { "path": "data/nmea" },
    "csv":  { "path": "data/csv" }
  },
  "webserver_server": { "enabled": true, "port": 8081 }
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
    "nmea": { "path": "data/nmea" },
    "csv":  { "path": "data/csv" }
  },
  "webserver_server": { "enabled": true, "port": 8081 }
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
    "nmea": { "path": "data/nmea" },
    "csv":  { "path": "data/csv" }
  },
  "webserver_server": { "enabled": true, "port": 8081 }
}
```

### Example with repeater (TCP server mode)

```json
{
  "server": { "port": 2000 },
  "repeater": {
    "mode": "server",
    "listenHost": "0.0.0.0",
    "remotePort": 2010,
    "protocol": "tcpip"
  },
  "output": {
    "nmea": { "path": "data/nmea" },
    "csv":  { "path": "data/csv" }
  },
  "webserver_server": { "enabled": true, "port": 8081 }
}
```

### Example with repeater disabled

```json
{
  "server": { "port": 2000 },
  "repeater": {
    "enabled": false,
    "mode": "server",
    "listenHost": "0.0.0.0",
    "remotePort": 2010,
    "protocol": "tcpip"
  },
  "output": {
    "nmea": { "path": "data/nmea" },
    "csv":  { "path": "data/csv" }
  },
  "webserver_server": { "enabled": true, "port": 8081 }
}
```

### Output web server

Enable `webserver_server` to serve the output directories over HTTP:

- `http://localhost:8081/` shows links
- `http://localhost:8081/nmea/` lists raw `.nmea` outputs
- `http://localhost:8081/csv/` lists `.csv` outputs

### Note about relative output paths and Docker

If you use relative paths like:

```json
"path": "data/nmea_logs"
```

They are resolved relative to the **working directory**. In Docker, the working directory is `/app`, so outputs go to:

- `/app/data/nmea`
- `/app/data/csv`

To persist outputs, mount a host folder (or volume) to `/app/data`.

---

## License

MIT (see `LICENSE`).
