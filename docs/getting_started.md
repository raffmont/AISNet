# Getting Started

Follow these steps to get AISNet running and verify it is receiving AIS messages.

## 1. Clone the repository

```bash
git clone <repo-url>
cd AISNet
```

## 2. Review configuration

Open `config.json` and confirm the TCP port, output paths, and optional web server settings are correct for your environment. The default configuration listens on port `2000`, writes to `data/nmea` and `data/csv`, and can optionally host those outputs over HTTP on port `8081`.

## 3. Install and run AISNet

Choose one of the installation methods:

- **Local Python install:** follow the steps in [Installation](install.md#local-installation-python).
- **Docker/Docker Compose:** follow the steps in [Installation](install.md#docker).

## 4. Verify the server

Open a second terminal and connect to the TCP server:

```bash
nc 127.0.0.1 2000
```

Paste a sample NMEA sentence to confirm it is logged and written to the output files.

## 5. Inspect output files

By default, AISNet writes:

- Raw sentences to `data/nmea` as rotating `.nmea` files
- Position reports to `data/csv` as rotating `.csv` files

If you are running via Docker, ensure `./data` is mounted to `/app/data` so these outputs persist.

If `webserver_server.enabled` is true, you can browse the outputs at `http://localhost:8081/` (links to `/nmea/` and `/csv/`).
