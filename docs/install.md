# Installation

This guide covers installing and running AISNet locally with Python or via Docker.

## Local installation (Python)

1. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create output directories:

   ```bash
   mkdir -p data/nmea_logs data/csv_logs
   ```

4. Run the server:

   ```bash
   python3 main.py -c config.json
   ```

5. Test the TCP server:

   ```bash
   nc 127.0.0.1 2000
   ```

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
