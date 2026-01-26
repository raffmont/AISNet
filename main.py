#!/usr/bin/env python3
"""
AISNet - TCP NMEA0183 AIS receiver with decoding, raw NMEA logging, CSV export, and optional repeating.

Features
- TCP server listening on configured port
- Receives NMEA 0183 sentences line-delimited
- Logs received sentences via logging
- Writes ALL raw received sentences to rotating .nmea files:
    YYYY/MM/DD/aisnet_YYYYMMDDZhh00.nmea  (UTC hour bucket)
- Decodes AIS AIVDM/AIVDO (single + multi-fragment reassembly) using pyais
- For each valid AIS position report (types 1,2,3,18,19,27) append a CSV row:
    TIMESTAMP, MMSI, LON, LAT, HEADING, SPEED
  with TIMESTAMP as ISO 8601 UTC
- Rotates .nmea and .csv files at server start and every configured number of seconds
- Optional repeater:
    If repeater.remoteHost and repeater.remotePort are set, each received raw AIS sentence
    (!AIVDM/!AIVDO) is forwarded to the remote endpoint using TCP or UDP (configurable).
    For UDP, broadcast can be enabled.

Run
  python3 main.py -c config.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from pyais import decode as ais_decode
except ImportError:
    ais_decode = None  # type: ignore

BUF_SIZE = 4096
FRAG_TTL_S = 30


# ----------------------------
# Logging
# ----------------------------
def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


# ----------------------------
# Time helpers
# ----------------------------
def utc_stamp_compact(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dZ%H%M%S")


def utc_hour_stamp(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dZ%H") + "00"


def utc_date_path(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y/%m/%d")


def utc_iso8601_z(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ----------------------------
# Config
# ----------------------------
@dataclass
class OutputCfg:
    path: str
    rotate_seconds: int


@dataclass
class RepeaterCfg:
    remoteHost: Optional[str] = None
    remotePort: Optional[int] = None
    protocol: str = "tcpip"  # "tcpip" or "udp"
    broadcast: bool = False  # UDP only

    def enabled(self) -> bool:
        return bool(self.remoteHost) and bool(self.remotePort)


@dataclass
class ServerConfig:
    port: int
    nmea: OutputCfg
    csv: OutputCfg
    repeater: RepeaterCfg

    @staticmethod
    def from_json(path: str) -> "ServerConfig":
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        try:
            port = int(cfg["server"]["port"])

            # output
            nmea = OutputCfg(
                path=str(cfg["output"]["nmea"]["path"]),
                rotate_seconds=int(cfg["output"]["nmea"]["rotate_seconds"]),
            )
            csv_out = OutputCfg(
                path=str(cfg["output"]["csv"]["path"]),
                rotate_seconds=int(cfg["output"]["csv"]["rotate_seconds"]),
            )

            # repeater (optional)
            rep_cfg_raw = cfg.get("repeater", {}) or {}
            repeater = RepeaterCfg(
                remoteHost=rep_cfg_raw.get("remoteHost"),
                remotePort=int(rep_cfg_raw["remotePort"]) if rep_cfg_raw.get("remotePort") is not None else None,
                protocol=str(rep_cfg_raw.get("protocol", "tcpip")).lower(),
                broadcast=bool(rep_cfg_raw.get("broadcast", False)),
            )

        except Exception as e:
            raise ValueError(f"Invalid config structure: {e}") from e

        if not (1 <= port <= 65535):
            raise ValueError("server.port must be in 1..65535")
        if nmea.rotate_seconds <= 0:
            raise ValueError("output.nmea.rotate_seconds must be > 0")
        if csv_out.rotate_seconds <= 0:
            raise ValueError("output.csv.rotate_seconds must be > 0")

        if repeater.enabled():
            if not (1 <= int(repeater.remotePort) <= 65535):
                raise ValueError("repeater.remotePort must be in 1..65535")
            if repeater.protocol not in ("tcpip", "udp"):
                raise ValueError('repeater.protocol must be "tcpip" or "udp"')

        return ServerConfig(port=port, nmea=nmea, csv=csv_out, repeater=repeater)


# ----------------------------
# Rotating writers
# ----------------------------
class RotatingTextWriter:
    """Rotating line-based text writer for .nmea."""

    def __init__(self, out_dir: Union[str, Path], rotate_seconds: int, suffix: str) -> None:
        self.out_dir = Path(out_dir)
        self.rotate_seconds = rotate_seconds
        self.suffix = suffix
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self._fh = None
        self._opened_ts = 0.0
        self._current_path: Optional[Path] = None

        self.rotate(force=True)

    def _make_filename(self) -> Path:
        return self.out_dir / utc_date_path() / f"aisnet_{utc_hour_stamp()}.{self.suffix}"

    def rotate(self, force: bool = False) -> None:
        now = time.time()
        if (not force) and self._fh and (now - self._opened_ts) < self.rotate_seconds:
            return

        if self._fh:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logging.exception("Failed closing %s file: %s", self.suffix, self._current_path)

        self._current_path = self._make_filename()
        self._current_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._current_path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._opened_ts = now
        logging.info("%s file opened: %s", self.suffix.upper(), self._current_path)

    def write_line(self, line: str) -> None:
        self.rotate(force=False)
        if not self._fh:
            self.rotate(force=True)
        self._fh.write(line.rstrip("\r\n") + "\n")

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logging.exception("Failed closing %s file: %s", self.suffix, self._current_path)
            finally:
                self._fh = None


class RotatingCsvWriter:
    """Rotating CSV writer with header."""

    HEADER = ["TIMESTAMP", "MMSI", "LON", "LAT", "HEADING", "SPEED"]

    def __init__(self, out_dir: Union[str, Path], rotate_seconds: int) -> None:
        self.out_dir = Path(out_dir)
        self.rotate_seconds = rotate_seconds
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self._fh = None
        self._writer: Optional[csv.writer] = None
        self._opened_ts = 0.0
        self._current_path: Optional[Path] = None

        self.rotate(force=True)

    def _make_filename(self) -> Path:
        return self.out_dir / utc_date_path() / f"aisnet_{utc_hour_stamp()}.csv"

    def rotate(self, force: bool = False) -> None:
        now = time.time()
        if (not force) and self._fh and (now - self._opened_ts) < self.rotate_seconds:
            return

        if self._fh:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logging.exception("Failed closing CSV file: %s", self._current_path)

        self._current_path = self._make_filename()
        self._current_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._current_path, "a", encoding="utf-8", newline="")
        self._writer = csv.writer(self._fh)

        # If file is new/empty, write header
        try:
            if self._current_path.stat().st_size == 0:
                self._writer.writerow(self.HEADER)
                self._fh.flush()
        except FileNotFoundError:
            self._writer.writerow(self.HEADER)
            self._fh.flush()

        self._opened_ts = now
        logging.info("CSV file opened: %s", self._current_path)

    def append_row(self, timestamp: str, mmsi: Any, lon: Any, lat: Any, heading: Any, speed: Any) -> None:
        self.rotate(force=False)
        if not self._writer or not self._fh:
            self.rotate(force=True)
        self._writer.writerow([timestamp, mmsi, lon, lat, heading, speed])
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logging.exception("Failed closing CSV file: %s", self._current_path)
            finally:
                self._fh = None
                self._writer = None


# ----------------------------
# Repeater
# ----------------------------
class Repeater:
    """
    Forwards raw AIS NMEA sentences to a remote endpoint.
    - TCP: maintains a connection and reconnects on failure
    - UDP: sends datagrams; can enable broadcast
    """

    def __init__(self, cfg: RepeaterCfg) -> None:
        self.cfg = cfg
        self._tcp_sock: Optional[socket.socket] = None
        self._udp_sock: Optional[socket.socket] = None

        if not cfg.enabled():
            return

        if cfg.protocol == "udp":
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if cfg.broadcast:
                self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def _tcp_connect(self) -> None:
        if not self.cfg.enabled():
            return
        if self.cfg.protocol != "tcpip":
            return
        if self._tcp_sock:
            return

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((self.cfg.remoteHost, int(self.cfg.remotePort)))
        s.settimeout(None)
        self._tcp_sock = s
        logging.info("Repeater TCP connected to %s:%s", self.cfg.remoteHost, self.cfg.remotePort)

    def _tcp_close(self) -> None:
        if self._tcp_sock:
            try:
                self._tcp_sock.close()
            except Exception:
                pass
            self._tcp_sock = None

    def forward(self, line: str) -> None:
        """
        Forward one raw AIS NMEA sentence.
        The line is sent as ASCII with CRLF termination.
        """
        if not self.cfg.enabled():
            return

        payload = (line.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore")

        if self.cfg.protocol == "udp":
            assert self._udp_sock is not None
            try:
                self._udp_sock.sendto(payload, (self.cfg.remoteHost, int(self.cfg.remotePort)))
            except Exception:
                logging.exception("Repeater UDP send failed to %s:%s", self.cfg.remoteHost, self.cfg.remotePort)
            return

        # TCP
        try:
            self._tcp_connect()
            assert self._tcp_sock is not None
            self._tcp_sock.sendall(payload)
        except Exception:
            logging.exception("Repeater TCP send failed to %s:%s (will reconnect)", self.cfg.remoteHost, self.cfg.remotePort)
            self._tcp_close()

    def close(self) -> None:
        self._tcp_close()
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None


# ----------------------------
# AIS decoding & extraction
# ----------------------------
def strip_nmea_line_endings(line: str) -> str:
    return line.strip("\r\n")


def is_aivdm(line: str) -> bool:
    return line.startswith("!AIVDM") or line.startswith("!AIVDO")


@dataclass
class FragSet:
    total: int
    parts: Dict[int, bytes] = field(default_factory=dict)  # ALWAYS bytes
    created_ts: float = field(default_factory=time.time)

    def add(self, frag_num: int, line: str) -> None:
        self.parts[frag_num] = strip_nmea_line_endings(line).encode("ascii", errors="ignore")
        self.created_ts = time.time()

    def is_complete(self) -> bool:
        return len(self.parts) == self.total and all(i in self.parts for i in range(1, self.total + 1))

    def assembled_lines_in_order(self) -> List[bytes]:
        return [self.parts[i] for i in range(1, self.total + 1)]


def parse_aivdm_header_fields(line: str) -> Optional[Tuple[int, int, str, str, str]]:
    fields = strip_nmea_line_endings(line).split(",")
    if len(fields) < 7:
        return None
    talker = fields[0]
    if talker not in ("!AIVDM", "!AIVDO"):
        return None
    try:
        total = int(fields[1])
        num = int(fields[2])
    except ValueError:
        return None
    seq_id = fields[3] or ""
    channel = fields[4] or ""
    return total, num, seq_id, channel, talker


def decode_pyais(line_or_lines) -> Optional[dict]:
    """
    Returns decoded dict when possible, else None.

    IMPORTANT:
      - pyais.decode expects bytes fragments as *positional args*
      - multi-fragment must call ais_decode(*payload)
    """
    if ais_decode is None:
        return None

    def to_bytes(x) -> bytes:
        if isinstance(x, bytes):
            return x
        s = strip_nmea_line_endings(str(x))
        return s.encode("ascii", errors="ignore")

    if isinstance(line_or_lines, (list, tuple)):
        payload: List[bytes] = [to_bytes(l) for l in line_or_lines]
        msg = ais_decode(*payload)  # splat parts as positional args
    else:
        payload_b = to_bytes(line_or_lines)
        msg = ais_decode(payload_b)

    if hasattr(msg, "asdict") and callable(getattr(msg, "asdict")):
        d = msg.asdict()
        return d if isinstance(d, dict) else None

    if hasattr(msg, "to_dict") and callable(getattr(msg, "to_dict")):
        d = msg.to_dict()
        return d if isinstance(d, dict) else None

    try:
        d = dict(msg)  # type: ignore
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def extract_csv_fields(d: dict) -> Optional[Tuple[str, int, float, float, Optional[float], Optional[float]]]:
    """
    Extract TIMESTAMP, MMSI, LON, LAT, HEADING, SPEED from decoded dict.
    Only accepts AIS position-report message types: 1,2,3,18,19,27.
    """
    msg_type = d.get("msg_type", d.get("message_type", d.get("type")))
    try:
        msg_type_i = int(msg_type) if msg_type is not None else None
    except Exception:
        msg_type_i = None

    if msg_type_i not in {1, 2, 3, 18, 19, 27}:
        return None

    mmsi = d.get("mmsi")
    lon = d.get("lon", d.get("longitude"))
    lat = d.get("lat", d.get("latitude"))

    heading = d.get("heading", d.get("true_heading"))
    speed = d.get("sog", d.get("speed"))

    if mmsi is None or lon is None or lat is None:
        return None

    def to_float(x) -> Optional[float]:
        if x is None:
            return None
        try:
            return float(x)
        except Exception:
            return None

    try:
        mmsi_i = int(mmsi)
    except Exception:
        return None

    lon_f = to_float(lon)
    lat_f = to_float(lat)
    if lon_f is None or lat_f is None:
        return None

    heading_f = to_float(heading)
    if heading_f is not None and heading_f >= 511:
        heading_f = None

    speed_f = to_float(speed)
    if speed_f is not None and speed_f >= 102.3:
        speed_f = None

    return utc_iso8601_z(), mmsi_i, lon_f, lat_f, heading_f, speed_f


class AisReassembler:
    def __init__(self) -> None:
        self._sets: Dict[Tuple[str, str, str], FragSet] = {}

    def cleanup(self) -> None:
        now = time.time()
        dead = [k for k, fs in self._sets.items() if (now - fs.created_ts) > FRAG_TTL_S]
        for k in dead:
            fs = self._sets.pop(k, None)
            if fs:
                logging.warning(
                    "Dropping incomplete AIS fragments (ttl): key=%s received=%d/%d",
                    k, len(fs.parts), fs.total
                )

    def push_and_decode(self, line: str, client_key: str) -> Optional[dict]:
        hdr = parse_aivdm_header_fields(line)
        if hdr is None:
            return None

        total, num, seq_id, channel, talker = hdr

        if total <= 1:
            return decode_pyais(line)

        key = (talker, channel, seq_id if seq_id else f"client:{client_key}")

        fs = self._sets.get(key)
        if fs is None or fs.total != total:
            fs = FragSet(total=total)
            self._sets[key] = fs

        fs.add(num, line)

        if fs.is_complete():
            parts = fs.assembled_lines_in_order()  # List[bytes]
            self._sets.pop(key, None)
            try:
                return decode_pyais(parts)
            except Exception as e:
                logging.error("AIS multi-frag decode failed: %s; part_types=%s",
                              e, [type(x).__name__ for x in parts])
                raise

        return None


# ----------------------------
# TCP server
# ----------------------------
def handle_client(
    conn: socket.socket,
    addr,
    nmea_writer: RotatingTextWriter,
    csv_writer: RotatingCsvWriter,
    repeater: Optional[Repeater],
) -> None:
    logging.info("Client connected: %s:%d", addr[0], addr[1])
    client_key = f"{addr[0]}:{addr[1]}"

    buf = b""
    reasm = AisReassembler()

    with conn:
        try:
            while True:
                chunk = conn.recv(BUF_SIZE)
                if not chunk:
                    break
                buf += chunk

                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    raw_line = raw_line.rstrip(b"\r")
                    if not raw_line:
                        continue

                    line = raw_line.decode("utf-8", errors="replace")

                    # Save raw sentence (all NMEA)
                    nmea_writer.write_line(line)
                    logging.info("NMEA RX: %s", line)

                    # Repeat ONLY raw AIS sentences
                    if repeater and is_aivdm(line):
                        repeater.forward(line)

                    # Decode AIS + append CSV row (only position reports)
                    if is_aivdm(line) and ais_decode is not None:
                        try:
                            reasm.cleanup()
                            d = reasm.push_and_decode(line, client_key=client_key)
                            if d:
                                row = extract_csv_fields(d)
                                if row:
                                    ts, mmsi, lon, lat, heading, speed = row
                                    csv_writer.append_row(ts, mmsi, lon, lat, heading, speed)
                        except Exception:
                            logging.exception("AIS decode/CSV append failed")

        except Exception:
            logging.exception("Error while handling client %s:%d", addr[0], addr[1])
        finally:
            logging.info("Client disconnected: %s:%d", addr[0], addr[1])


def run_server(cfg: ServerConfig) -> None:
    setup_logging()

    if ais_decode is None:
        logging.warning("pyais is not installed. Run: pip install -r requirements.txt")

    nmea_writer = RotatingTextWriter(cfg.nmea.path, cfg.nmea.rotate_seconds, suffix="nmea")
    csv_writer = RotatingCsvWriter(cfg.csv.path, cfg.csv.rotate_seconds)
    repeater = Repeater(cfg.repeater) if cfg.repeater.enabled() else None

    try:
        logging.info("Starting TCP server on 0.0.0.0:%d", cfg.port)
        if repeater:
            logging.info("Repeater enabled: %s://%s:%s (broadcast=%s)",
                         cfg.repeater.protocol, cfg.repeater.remoteHost, cfg.repeater.remotePort, cfg.repeater.broadcast)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", cfg.port))
            srv.listen(5)
            logging.info("Listening...")

            while True:
                conn, addr = srv.accept()
                handle_client(conn, addr, nmea_writer, csv_writer, repeater)

    finally:
        if repeater:
            repeater.close()
        nmea_writer.close()
        csv_writer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="AIS NMEA TCP server with rotating NMEA + CSV outputs and repeater")
    parser.add_argument("-c", "--config", default="config.json", help="Path to JSON configuration file")
    args = parser.parse_args()

    cfg = ServerConfig.from_json(args.config)
    run_server(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
